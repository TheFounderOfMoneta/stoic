from __future__ import annotations

from datetime import UTC
import json
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message

from .config import TelegramSettings
from .models import TelegramDialogSummary, TelegramMessageRecord


class TelegramUserClient:
    def __init__(self, settings: TelegramSettings) -> None:
        self.settings = settings
        session = _telegram_session(settings)
        self._client = TelegramClient(
            session,
            settings.api_id,
            settings.api_hash,
            app_version=settings.app_title,
            device_model="Stoic Ingest",
            system_version="Windows",
        )

    @property
    def raw_client(self) -> TelegramClient:
        return self._client

    async def connect(self) -> None:
        await self._client.connect()

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def is_authorized(self) -> bool:
        return await self._client.is_user_authorized()

    async def request_login_code(self, phone: str) -> dict[str, Any]:
        sent = await self._client.send_code_request(phone)
        return {
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
            "type": sent.type.__class__.__name__,
            "timeout": getattr(sent, "timeout", None),
        }

    async def sign_in(
        self,
        *,
        phone: str,
        code: str,
        phone_code_hash: str,
        password: str | None = None,
    ) -> dict[str, Any]:
        try:
            user = await self._client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                raise ValueError("Telegram account requires 2FA password.") from None
            user = await self._client.sign_in(password=password)

        return {
            "id": user.id,
            "username": user.username,
            "phone": user.phone,
            "first_name": user.first_name,
            "last_name": user.last_name,
        }

    async def me(self) -> dict[str, Any] | None:
        me = await self._client.get_me()
        if me is None:
            return None
        return {
            "id": me.id,
            "username": me.username,
            "phone": me.phone,
            "first_name": me.first_name,
            "last_name": me.last_name,
        }

    def export_session_string(self) -> str:
        return StringSession.save(self._client.session)

    async def list_dialogs(self, limit: int = 50) -> list[TelegramDialogSummary]:
        dialogs: list[TelegramDialogSummary] = []
        position = 0
        async for dialog in self._client.iter_dialogs(limit=limit):
            if not (dialog.is_user or dialog.is_group):
                continue
            position += 1
            dialogs.append(
                TelegramDialogSummary(
                    id=dialog.id,
                    title=dialog.title,
                    entity_type=dialog.entity.__class__.__name__,
                    kind="group" if dialog.is_group else "contact",
                    username=getattr(dialog.entity, "username", None),
                    unread_count=dialog.unread_count,
                    message_count_hint=getattr(dialog, "message_count", None),
                    pinned=bool(getattr(dialog, "pinned", False)),
                    position=position,
                    last_message_at=_normalize_datetime(dialog.date) if dialog.date else None,
                    last_message_text=getattr(dialog.message, "message", None),
                )
            )
        return dialogs

    async def list_messages(self, entity: str, limit: int = 100) -> list[TelegramMessageRecord]:
        target = await self._resolve_entity(entity)
        title = getattr(target, "title", None) or getattr(target, "username", None) or str(getattr(target, "id", entity))
        records: list[TelegramMessageRecord] = []

        async for message in self._client.iter_messages(target, limit=limit):
            records.append(
                TelegramMessageRecord(
                    id=message.id,
                    chat_id=getattr(message.peer_id, "channel_id", None)
                    or getattr(message.peer_id, "chat_id", None)
                    or getattr(message.peer_id, "user_id", 0),
                    chat_title=title,
                    sender_id=message.sender_id,
                    sender_name=_sender_name(message),
                    text=message.message,
                    outgoing=bool(getattr(message, "out", False)),
                    sent_at=_normalize_datetime(message.date),
                    raw=message.to_dict(),
                )
            )

        records.sort(key=lambda item: item.sent_at)
        return records

    async def send_message(self, entity: str, text: str) -> TelegramMessageRecord:
        target = await self._resolve_entity(entity)
        sent = await self._client.send_message(target, text)
        title = getattr(target, "title", None) or getattr(target, "username", None) or str(getattr(target, "id", entity))
        return TelegramMessageRecord(
            id=sent.id,
            chat_id=getattr(sent.peer_id, "channel_id", None)
            or getattr(sent.peer_id, "chat_id", None)
            or getattr(sent.peer_id, "user_id", 0),
            chat_title=title,
            sender_id=sent.sender_id,
            sender_name=_sender_name(sent),
            text=sent.message,
            outgoing=bool(getattr(sent, "out", False)),
            sent_at=_normalize_datetime(sent.date),
            raw=sent.to_dict(),
        )

    async def _resolve_entity(self, entity: str):
        normalized = entity.strip()
        if normalized.lstrip("-").isdigit():
            return await self._client.get_entity(int(normalized))
        return await self._client.get_entity(normalized)

    async def export_messages(self, entity: str, limit: int = 100, output: Path | None = None) -> Path:
        messages = await self.list_messages(entity=entity, limit=limit)
        export_path = output or self.settings.export_dir / f"{_safe_entity_name(entity)}.json"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(
            json.dumps([item.model_dump(mode="json") for item in messages], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return export_path


def _normalize_datetime(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sender_name(message: Message) -> str | None:
    sender = getattr(message, "sender", None)
    if sender is None:
        return None
    first_name = getattr(sender, "first_name", None) or ""
    last_name = getattr(sender, "last_name", None) or ""
    username = getattr(sender, "username", None)
    full_name = f"{first_name} {last_name}".strip()
    return full_name or username


def _safe_entity_name(entity: str) -> str:
    text = entity.strip() or "dialog"
    return "".join(char if char.isalnum() or char in "-._" else "_" for char in text)


def _telegram_session(settings: TelegramSettings):
    if settings.session_string:
        return StringSession(settings.session_string)

    legacy_session = settings.session_path.with_suffix(".session")
    if legacy_session.exists():
        return str(settings.session_path)

    return StringSession()
