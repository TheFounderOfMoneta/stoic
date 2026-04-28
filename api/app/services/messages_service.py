from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from ..config import Settings
from ..schemas import (
    AssistantMessageSendRequest,
    AssistantMessageSendResponse,
    MessageConversationSummary,
    MessageItem,
    TelegramAuthCodeRequest,
    TelegramAuthCodeResponse,
    TelegramAuthSignInRequest,
    TelegramAuthStatusResponse,
    TelegramAuthUserResponse,
    TelegramDialogSummaryResponse,
    TelegramImportResponse,
    TelegramDialogPinRequest,
    TelegramSendMessageRequest,
)
from .codex_service import run_codex_chat


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _conversation_id_for_row(row: dict[str, Any]) -> str:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    conversation = metadata.get("conversation") if isinstance(metadata, dict) else None
    if isinstance(conversation, dict) and isinstance(conversation.get("id"), str) and conversation["id"]:
        return conversation["id"]

    telegram = metadata.get("telegram") if isinstance(metadata, dict) else None
    if isinstance(telegram, dict) and telegram.get("chat_id") is not None:
        return f"telegram:{telegram['chat_id']}"

    return row["id"]


def _channel_for_row(row: dict[str, Any]) -> str:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    channel = metadata.get("channel") if isinstance(metadata, dict) else None
    if isinstance(channel, str) and channel in {"assistant", "telegram"}:
        return channel
    if row.get("origin") == "telegram":
        return "telegram"
    return "assistant"


def _sender_role_for_row(row: dict[str, Any]) -> str:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    role = metadata.get("sender_role") if isinstance(metadata, dict) else None
    if isinstance(role, str) and role in {"user", "assistant", "contact", "system"}:
        return role
    if _channel_for_row(row) == "telegram":
        return "contact"
    return "assistant"


def _sender_name_for_row(row: dict[str, Any]) -> str | None:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    value = metadata.get("sender_name") if isinstance(metadata, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()

    telegram = metadata.get("telegram") if isinstance(metadata, dict) else None
    if isinstance(telegram, dict):
        raw_name = telegram.get("sender_name")
        if isinstance(raw_name, str) and raw_name.strip():
            return raw_name.strip()
    return None


def _conversation_title_for_row(row: dict[str, Any]) -> str:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    conversation = metadata.get("conversation") if isinstance(metadata, dict) else None
    if isinstance(conversation, dict):
        title = conversation.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()

    telegram = metadata.get("telegram") if isinstance(metadata, dict) else None
    if isinstance(telegram, dict):
        title = telegram.get("chat_title")
        if isinstance(title, str) and title.strip():
            return title.strip()

    text_value = (row.get("raw_text") or "").strip()
    return text_value[:60] or "Новый чат"


def _external_message_id_for_row(row: dict[str, Any]) -> str | None:
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {}
    telegram = metadata.get("telegram") if isinstance(metadata, dict) else None
    if isinstance(telegram, dict) and telegram.get("message_id") is not None:
        return str(telegram["message_id"])

    assistant = metadata.get("assistant") if isinstance(metadata, dict) else None
    if isinstance(assistant, dict) and isinstance(assistant.get("codex_conversation_id"), str):
        return assistant["codex_conversation_id"]
    return None


def _message_item_from_row(row: dict[str, Any]) -> MessageItem:
    return MessageItem(
        id=row["id"],
        conversation_id=_conversation_id_for_row(row),
        channel=_channel_for_row(row),  # type: ignore[arg-type]
        sender_role=_sender_role_for_row(row),  # type: ignore[arg-type]
        text=(row.get("raw_text") or "").strip(),
        captured_at=row["captured_at"],
        sender_name=_sender_name_for_row(row),
        external_message_id=_external_message_id_for_row(row),
        metadata_json=row.get("metadata_json"),
    )


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _telegram_dialog_label(
    *,
    title: str | None,
    username: str | None,
    entity: str | None,
    chat_id: int,
) -> str:
    for candidate in (title, username, entity):
        if isinstance(candidate, str):
            normalized = candidate.strip()
            if normalized:
                return normalized
    return f"Telegram {chat_id}"


def _telegram_dialog_sort_key(item: TelegramDialogSummaryResponse) -> tuple[int, int, float, int, str]:
    pinned_rank = 0 if item.app_pinned else 1
    telegram_rank = 0 if item.telegram_pinned else 1
    position_rank = item.position if item.position is not None else 10**9
    if item.last_message_at is not None:
        timestamp_rank = -item.last_message_at.timestamp()
    else:
        timestamp_rank = float("inf")
    return (pinned_rank, telegram_rank, position_rank, timestamp_rank, item.title.casefold())


async def list_message_conversations(
    session: AsyncSession,
    *,
    channel: str | None,
    limit: int,
    offset: int,
) -> list[MessageConversationSummary]:
    stmt = (
        select(
            models.sources.c.id,
            models.sources.c.origin,
            models.sources.c.captured_at,
            models.sources.c.raw_text,
            models.sources.c.metadata_json,
        )
        .where(models.sources.c.source_type == "chat_message")
        .order_by(models.sources.c.captured_at.desc(), models.sources.c.id.desc())
    )
    rows = [dict(row) for row in (await session.execute(stmt)).mappings().all()]

    grouped: dict[str, MessageConversationSummary] = {}
    for row in rows:
        row_channel = _channel_for_row(row)
        if channel and row_channel != channel:
            continue

        conversation_id = _conversation_id_for_row(row)
        existing = grouped.get(conversation_id)
        if existing is None:
            grouped[conversation_id] = MessageConversationSummary(
                id=conversation_id,
                channel=row_channel,  # type: ignore[arg-type]
                title=_conversation_title_for_row(row),
                last_message_text=(row.get("raw_text") or "").strip() or None,
                last_message_at=row["captured_at"],
                message_count=1,
                sender_name=_sender_name_for_row(row),
            )
            continue

        existing.message_count += 1

    items = list(grouped.values())
    return items[offset : offset + limit]


async def list_conversation_messages(
    session: AsyncSession,
    *,
    conversation_id: str,
    limit: int,
    offset: int,
    after: str | None = None,
) -> list[MessageItem]:
    stmt = select(
        models.sources.c.id,
        models.sources.c.origin,
        models.sources.c.captured_at,
        models.sources.c.raw_text,
        models.sources.c.metadata_json,
    ).where(models.sources.c.source_type == "chat_message")

    if conversation_id.startswith("telegram:"):
        chat_id = conversation_id.split(":", 1)[1]
        stmt = stmt.where(text("(metadata_json->'telegram'->>'chat_id') = :chat_id")).params(chat_id=chat_id)
    else:
        stmt = stmt.where(text("(metadata_json->'conversation'->>'id') = :conversation_id")).params(
            conversation_id=conversation_id
        )

    after_dt = _parse_iso_datetime(after)
    if after_dt is not None:
        stmt = stmt.where(models.sources.c.captured_at > after_dt)

    stmt = stmt.order_by(models.sources.c.captured_at, models.sources.c.id).limit(limit).offset(offset)
    rows = [dict(row) for row in (await session.execute(stmt)).mappings().all()]
    return [_message_item_from_row(row) for row in rows]


async def send_assistant_message(
    session: AsyncSession,
    *,
    settings: Settings,
    payload: AssistantMessageSendRequest,
) -> AssistantMessageSendResponse:
    conversation_id = payload.conversation_id or str(uuid4())
    sent_at = _utcnow()
    user_message_id = str(uuid4())

    user_metadata = {
        "channel": "assistant",
        "sender_role": "user",
        "conversation": {
            "id": conversation_id,
            "title": payload.message.strip()[:60] or "Новый чат",
        },
        "attachments": [
            {
                "name": item.name,
                "mime_type": item.mime_type,
                "has_text": item.text is not None,
                "has_binary": item.data_base64 is not None,
            }
            for item in payload.attachments
        ],
    }
    await session.execute(
        insert(models.sources).values(
            id=user_message_id,
            source_type="chat_message",
            origin="manual_input",
            source_account_id=None,
            url_or_path=None,
            captured_at=sent_at,
            status="captured",
            raw_text=payload.message.strip(),
            raw_blob_ref=None,
            metadata_json=user_metadata,
        )
    )

    try:
        codex_response = await run_codex_chat(
            settings=settings,
            message=payload.message,
            conversation_id=payload.conversation_id,
            model=payload.model,
            reasoning_effort=payload.reasoning_effort,
            sandbox=payload.sandbox,
            approval_policy=payload.approval_policy,
            attachments=payload.attachments,
        )
    except HTTPException as exc:
        error_message_id = str(uuid4())
        await session.execute(
            insert(models.sources).values(
                id=error_message_id,
                source_type="chat_message",
                origin="manual_input",
                source_account_id=None,
                url_or_path=None,
                captured_at=_utcnow(),
                status="error",
                raw_text=str(exc.detail),
                raw_blob_ref=None,
                metadata_json={
                    "channel": "assistant",
                    "sender_role": "system",
                    "conversation": {
                        "id": conversation_id,
                        "title": payload.message.strip()[:60] or "Новый чат",
                    },
                },
            )
        )
        raise

    assistant_message_id = str(uuid4())
    await session.execute(
        insert(models.sources).values(
            id=assistant_message_id,
            source_type="chat_message",
            origin="manual_input",
            source_account_id=None,
            url_or_path=None,
            captured_at=_utcnow(),
            status="captured",
            raw_text=codex_response["message"].strip(),
            raw_blob_ref=None,
            metadata_json={
                "channel": "assistant",
                "sender_role": "assistant",
                "conversation": {
                    "id": conversation_id,
                    "title": payload.message.strip()[:60] or "Новый чат",
                },
                "assistant": {
                    "codex_conversation_id": codex_response["conversation_id"],
                    "model": codex_response["model"],
                    "reasoning_effort": codex_response["reasoning_effort"],
                    "sandbox": codex_response["sandbox"],
                    "approval_policy": codex_response["approval_policy"],
                    "usage": codex_response["usage"].model_dump() if codex_response["usage"] else None,
                },
            },
        )
    )

    return AssistantMessageSendResponse(
        conversation_id=conversation_id,
        message=codex_response["message"],
        model=codex_response["model"],
        reasoning_effort=codex_response["reasoning_effort"],
        sandbox=codex_response["sandbox"],
        approval_policy=codex_response["approval_policy"],
        usage=codex_response["usage"],
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
    )


async def list_telegram_dialogs(
    session: AsyncSession,
    *,
    limit: int,
    changed_after: str | None = None,
) -> list[TelegramDialogSummaryResponse]:
    stmt = select(
        models.source_accounts.c.id,
        models.source_accounts.c.account_label,
        models.source_accounts.c.settings_json,
        models.source_accounts.c.updated_at,
    ).where(models.source_accounts.c.module_type == "telegram_dialog")
    changed_after_dt = _parse_iso_datetime(changed_after)
    if changed_after_dt is not None:
        stmt = stmt.where(models.source_accounts.c.updated_at > changed_after_dt)

    rows = (await session.execute(stmt)).mappings().all()
    items: list[TelegramDialogSummaryResponse] = []
    for row in rows:
        settings_json = row["settings_json"] if isinstance(row["settings_json"], dict) else {}
        chat_id = settings_json.get("chat_id")
        if chat_id is None:
            continue
        last_message_at = _parse_iso_datetime(settings_json.get("last_message_at"))
        app_pinned = bool(settings_json.get("app_pinned"))
        telegram_pinned = bool(settings_json.get("telegram_pinned"))
        position = settings_json.get("position")
        items.append(
            TelegramDialogSummaryResponse(
                id=int(chat_id),
                title=row["account_label"],
                entity=str(settings_json.get("entity") or chat_id),
                entity_type=str(settings_json.get("entity_type") or "Unknown"),
                kind=str(settings_json.get("kind") or "contact"),  # type: ignore[arg-type]
                username=settings_json.get("username") if isinstance(settings_json.get("username"), str) else None,
                unread_count=int(settings_json.get("unread_count") or 0),
                message_count_hint=int(settings_json["message_count_hint"]) if settings_json.get("message_count_hint") is not None else None,
                conversation_id=f"telegram:{chat_id}",
                app_pinned=app_pinned,
                is_pinned=app_pinned or telegram_pinned,
                telegram_pinned=telegram_pinned,
                position=int(position) if position is not None else None,
                last_message_at=last_message_at,
                last_message_text=settings_json.get("last_message_text") if isinstance(settings_json.get("last_message_text"), str) else None,
            )
        )
    items.sort(key=_telegram_dialog_sort_key)
    return items[:limit]


async def telegram_auth_status() -> TelegramAuthStatusResponse:
    client = await _build_telegram_client()
    try:
        await client.connect()
        authorized = await client.is_authorized()
        if not authorized:
            return TelegramAuthStatusResponse(authorized=False, user=None)

        me = await client.me()
        await _persist_telegram_runtime(client, phone=me["phone"] if me else None)
        if not me:
            return TelegramAuthStatusResponse(authorized=True, user=None)
        return TelegramAuthStatusResponse(authorized=True, user=TelegramAuthUserResponse(**me))
    finally:
        await client.disconnect()


async def telegram_request_code(payload: TelegramAuthCodeRequest) -> TelegramAuthCodeResponse:
    client = await _build_telegram_client()
    try:
        await client.connect()
        response = await client.request_login_code(payload.phone)
        return TelegramAuthCodeResponse(**response)
    finally:
        await client.disconnect()


async def telegram_sign_in(payload: TelegramAuthSignInRequest) -> TelegramAuthUserResponse:
    client = await _build_telegram_client()
    try:
        await client.connect()
        user = await client.sign_in(
            phone=payload.phone,
            code=payload.code,
            phone_code_hash=payload.phone_code_hash,
            password=payload.password,
        )
        await _persist_telegram_runtime(client, phone=user.get("phone") or payload.phone)
        return TelegramAuthUserResponse(**user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    finally:
        await client.disconnect()


async def telegram_sign_in_and_store(
    session: AsyncSession,
    *,
    payload: TelegramAuthSignInRequest,
) -> TelegramAuthUserResponse:
    user = await telegram_sign_in(payload)
    await _upsert_telegram_source_account(
        session,
        user=user,
        connected_at=_utcnow(),
    )
    await sync_telegram_dialogs_to_db()
    return user


async def list_telegram_messages(*, entity: str, limit: int) -> list[MessageItem]:
    client = await _build_telegram_client()
    try:
        await client.connect()
        if not await client.is_authorized():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Telegram session is not authorized.")

        try:
            records = await client.list_messages(entity=entity, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        connected_at = await _telegram_connected_at()
        if connected_at is not None:
            records = [record for record in records if record.sent_at >= connected_at]
        items: list[MessageItem] = []
        for record in records:
            sender_role = "user" if record.outgoing else "contact"
            metadata = {
                "channel": "telegram",
                "sender_role": sender_role,
                "sender_name": record.sender_name,
                "conversation": {
                    "id": f"telegram:{record.chat_id}",
                    "title": record.chat_title,
                },
                "telegram": {
                    "chat_id": record.chat_id,
                    "chat_title": record.chat_title,
                    "message_id": record.id,
                    "sender_id": record.sender_id,
                    "sender_name": record.sender_name,
                },
                "raw": record.raw,
            }
            items.append(
                MessageItem(
                    id=f"telegram:{record.chat_id}:{record.id}",
                    conversation_id=f"telegram:{record.chat_id}",
                    channel="telegram",
                    sender_role=sender_role,  # type: ignore[arg-type]
                    text=(record.text or "").strip(),
                    captured_at=record.sent_at,
                    sender_name=record.sender_name,
                    external_message_id=str(record.id),
                    metadata_json=metadata,
                )
            )
        return items
    finally:
        await client.disconnect()


async def import_telegram_messages(
    session: AsyncSession,
    *,
    entity: str,
    limit: int,
) -> TelegramImportResponse:
    client = await _build_telegram_client()
    try:
        await client.connect()
        if not await client.is_authorized():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Telegram session is not authorized.")

        try:
            records = await client.list_messages(entity=entity, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    finally:
        await client.disconnect()

    if not records:
        return TelegramImportResponse(conversation_id=f"telegram:{entity}", imported=0, skipped=0, total=0)

    chat_id = str(records[0].chat_id)
    message_ids = [str(record.id) for record in records]
    existing_ids = await _existing_telegram_message_ids(session, chat_id=chat_id, message_ids=message_ids)
    connected_at = await _telegram_connected_at(session)

    if connected_at is not None:
        records = [record for record in records if record.sent_at >= connected_at]
    if not records:
        return TelegramImportResponse(conversation_id=f"telegram:{chat_id}", imported=0, skipped=0, total=0)

    imported = 0
    skipped = 0
    transformer = _telegram_transformer()
    for record in records:
        if str(record.id) in existing_ids:
            skipped += 1
            continue

        source = transformer(record)
        metadata = source.get("metadata_json") if isinstance(source.get("metadata_json"), dict) else {}
        sender_role = "user" if record.outgoing else "contact"
        metadata.update(
            {
                "channel": "telegram",
                "sender_role": sender_role,
                "sender_name": record.sender_name,
                "conversation": {
                    "id": f"telegram:{record.chat_id}",
                    "title": record.chat_title,
                },
            }
        )
        await session.execute(
            insert(models.sources).values(
                id=str(uuid4()),
                source_type=source["source_type"],
                origin=source["origin"],
                source_account_id=None,
                url_or_path=source["url_or_path"],
                captured_at=source["captured_at"],
                status=source["status"],
                raw_text=source["raw_text"],
                raw_blob_ref=source["raw_blob_ref"],
                metadata_json=metadata,
            )
        )
        imported += 1

    return TelegramImportResponse(
        conversation_id=f"telegram:{chat_id}",
        imported=imported,
        skipped=skipped,
        total=len(records),
    )


async def send_telegram_message(
    session: AsyncSession,
    *,
    entity: str,
    payload: TelegramSendMessageRequest,
) -> MessageItem:
    client = await _build_telegram_client()
    try:
        await client.connect()
        if not await client.is_authorized():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Telegram session is not authorized.")

        try:
            record = await client.send_message(entity=entity, text=payload.text.strip())
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        await _persist_telegram_runtime(client, phone=None)
    finally:
        await client.disconnect()

    source_id = str(uuid4())
    await _upsert_telegram_dialog_account(
        session,
        chat_id=record.chat_id,
        title=record.chat_title,
        entity=entity,
        entity_type="Unknown",
        kind="contact",
        username=entity if not entity.lstrip("-").isdigit() else None,
        unread_count=0,
        message_count_hint=None,
        telegram_pinned=None,
        position=None,
        last_message_at=record.sent_at,
        last_message_text=(record.text or "").strip(),
    )
    metadata = {
        "channel": "telegram",
        "sender_role": "user",
        "sender_name": record.sender_name,
        "conversation": {
            "id": f"telegram:{record.chat_id}",
            "title": record.chat_title,
        },
        "telegram": {
            "chat_id": record.chat_id,
            "chat_title": record.chat_title,
            "message_id": record.id,
            "sender_id": record.sender_id,
            "sender_name": record.sender_name,
        },
        "raw": record.raw,
    }
    await session.execute(
        insert(models.sources).values(
            id=source_id,
            source_type="chat_message",
            origin="telegram",
            source_account_id="telegram:primary",
            url_or_path=None,
            captured_at=record.sent_at,
            status="captured",
            raw_text=(record.text or "").strip(),
            raw_blob_ref=None,
            metadata_json=metadata,
        )
    )
    return MessageItem(
        id=source_id,
        conversation_id=f"telegram:{record.chat_id}",
        channel="telegram",
        sender_role="user",
        text=(record.text or "").strip(),
        captured_at=record.sent_at,
        sender_name=record.sender_name,
        external_message_id=str(record.id),
        metadata_json=metadata,
    )


async def sync_telegram_dialogs_to_db(limit: int = 500) -> None:
    client = await _build_telegram_client()
    try:
        await client.connect()
        if not await client.is_authorized():
            return

        dialogs = await client.list_dialogs(limit=limit)
        from ..db import SessionLocal, _prepare_session

        async with SessionLocal() as session:
            async with session.begin():
                await _prepare_session(session, read_only=False)
                for dialog in dialogs:
                    await _upsert_telegram_dialog_account(
                        session,
                        chat_id=dialog.id,
                        title=dialog.title,
                        entity=dialog.username or str(dialog.id),
                        entity_type=dialog.entity_type,
                        kind=dialog.kind,
                        username=dialog.username,
                        unread_count=dialog.unread_count,
                        message_count_hint=dialog.message_count_hint,
                        telegram_pinned=dialog.pinned,
                        position=dialog.position,
                        last_message_at=dialog.last_message_at,
                        last_message_text=dialog.last_message_text,
                    )
    finally:
        await client.disconnect()


async def set_telegram_dialog_pin(
    session: AsyncSession,
    *,
    chat_id: int,
    payload: TelegramDialogPinRequest,
) -> TelegramDialogSummaryResponse:
    account_id = f"telegram:dialog:{chat_id}"
    stmt = select(
        models.source_accounts.c.account_label,
        models.source_accounts.c.settings_json,
    ).where(models.source_accounts.c.id == account_id).limit(1)
    row = (await session.execute(stmt)).mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telegram dialog not found.")

    settings_json = dict(row["settings_json"] or {})
    settings_json["app_pinned"] = payload.pinned
    now = _utcnow()
    await session.execute(
        update(models.source_accounts)
        .where(models.source_accounts.c.id == account_id)
        .values(settings_json=settings_json, updated_at=now)
    )

    chat_id_value = int(settings_json["chat_id"])
    return TelegramDialogSummaryResponse(
        id=chat_id_value,
        title=row["account_label"],
        entity=str(settings_json.get("entity") or chat_id_value),
        entity_type=str(settings_json.get("entity_type") or "Unknown"),
        kind=str(settings_json.get("kind") or "contact"),  # type: ignore[arg-type]
        username=settings_json.get("username") if isinstance(settings_json.get("username"), str) else None,
        unread_count=int(settings_json.get("unread_count") or 0),
        message_count_hint=int(settings_json["message_count_hint"]) if settings_json.get("message_count_hint") is not None else None,
        conversation_id=f"telegram:{chat_id_value}",
        app_pinned=bool(settings_json.get("app_pinned")),
        is_pinned=bool(settings_json.get("app_pinned")) or bool(settings_json.get("telegram_pinned")),
        telegram_pinned=bool(settings_json.get("telegram_pinned")),
        position=int(settings_json["position"]) if settings_json.get("position") is not None else None,
        last_message_at=_parse_iso_datetime(settings_json.get("last_message_at")),
        last_message_text=settings_json.get("last_message_text") if isinstance(settings_json.get("last_message_text"), str) else None,
    )


async def _existing_telegram_message_ids(
    session: AsyncSession,
    *,
    chat_id: str,
    message_ids: list[str],
) -> set[str]:
    if not message_ids:
        return set()

    stmt = (
        select(models.sources.c.metadata_json)
        .where(models.sources.c.source_type == "chat_message")
        .where(models.sources.c.origin == "telegram")
        .where(text("(metadata_json->'telegram'->>'chat_id') = :chat_id"))
        .where(text("(metadata_json->'telegram'->>'message_id') = ANY(:message_ids)"))
        .params(chat_id=chat_id, message_ids=message_ids)
    )
    rows = (await session.execute(stmt)).scalars().all()
    existing: set[str] = set()
    for metadata in rows:
        if isinstance(metadata, dict):
            telegram = metadata.get("telegram")
            if isinstance(telegram, dict) and telegram.get("message_id") is not None:
                existing.add(str(telegram["message_id"]))
    return existing


async def _upsert_telegram_source_account(
    session: AsyncSession,
    *,
    user: TelegramAuthUserResponse,
    connected_at: datetime,
) -> None:
    account_id = "telegram:primary"
    label = " ".join(part for part in [user.first_name, user.last_name] if part).strip() or user.username or user.phone or "Telegram"
    settings_json = {
        "connected_at": connected_at.isoformat(),
        "user": user.model_dump(),
        "sync_mode": "new_messages_only",
    }
    exists_stmt = select(models.source_accounts.c.id).where(models.source_accounts.c.id == account_id).limit(1)
    exists = (await session.scalar(exists_stmt)) is not None

    if exists:
        await session.execute(
            update(models.source_accounts)
            .where(models.source_accounts.c.id == account_id)
            .values(
                module_type="telegram",
                account_label=label,
                status="active",
                settings_json=settings_json,
                updated_at=connected_at,
            )
        )
        return

    await session.execute(
        insert(models.source_accounts).values(
            id=account_id,
            module_type="telegram",
            account_label=label,
            status="active",
            settings_json=settings_json,
            created_at=connected_at,
            updated_at=connected_at,
        )
    )


async def _upsert_telegram_dialog_account(
    session: AsyncSession,
    *,
    chat_id: int,
    title: str | None,
    entity: str,
    entity_type: str,
    kind: str,
    username: str | None,
    unread_count: int,
    message_count_hint: int | None,
    telegram_pinned: bool | None,
    position: int | None,
    last_message_at: datetime | None,
    last_message_text: str | None,
) -> None:
    account_id = f"telegram:dialog:{chat_id}"
    now = _utcnow()
    label = _telegram_dialog_label(title=title, username=username, entity=entity, chat_id=chat_id)
    existing_stmt = select(models.source_accounts.c.settings_json).where(models.source_accounts.c.id == account_id).limit(1)
    existing_settings = await session.scalar(existing_stmt)
    existing_dict = dict(existing_settings or {}) if isinstance(existing_settings, dict) else {}
    settings_json = {
        "chat_id": chat_id,
        "entity": entity,
        "entity_type": entity_type,
        "kind": kind,
        "username": username,
        "unread_count": unread_count,
        "message_count_hint": message_count_hint,
        "telegram_pinned": bool(existing_dict.get("telegram_pinned")) if telegram_pinned is None else telegram_pinned,
        "app_pinned": bool(existing_dict.get("app_pinned")),
        "position": existing_dict.get("position") if position is None else position,
        "last_message_at": last_message_at.isoformat() if last_message_at else None,
        "last_message_text": last_message_text,
    }
    exists_stmt = select(models.source_accounts.c.id).where(models.source_accounts.c.id == account_id).limit(1)
    exists = (await session.scalar(exists_stmt)) is not None
    if exists:
        await session.execute(
            update(models.source_accounts)
            .where(models.source_accounts.c.id == account_id)
            .values(
                module_type="telegram_dialog",
                account_label=label,
                status="active",
                settings_json=settings_json,
                updated_at=now,
            )
        )
        return

    await session.execute(
        insert(models.source_accounts).values(
            id=account_id,
            module_type="telegram_dialog",
            account_label=label,
            status="active",
            settings_json=settings_json,
            created_at=now,
            updated_at=now,
        )
    )


async def _telegram_connected_at(session: AsyncSession | None = None) -> datetime | None:
    own_session = session is None
    active_session = session
    if active_session is None:
        from ..db import SessionLocal, _prepare_session

        active_session = SessionLocal()
        await active_session.begin()
        await _prepare_session(active_session, read_only=True)

    try:
        stmt = select(models.source_accounts.c.settings_json).where(models.source_accounts.c.id == "telegram:primary").limit(1)
        settings_json = await active_session.scalar(stmt)
        if not isinstance(settings_json, dict):
            return None
        value = settings_json.get("connected_at")
        if not isinstance(value, str) or not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    finally:
        if own_session and active_session is not None:
            await active_session.close()


async def _build_telegram_client():
    try:
        _ensure_project_root_on_path()
        from data.ingest.telegram.client import TelegramUserClient
        from data.ingest.telegram.config import get_telegram_settings
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram ingest dependencies are not installed.",
        ) from exc

    return TelegramUserClient(get_telegram_settings())


def _telegram_transformer():
    _ensure_project_root_on_path()
    from data.ingest.telegram.transformers import telegram_message_to_source

    return telegram_message_to_source


def _ensure_project_root_on_path() -> None:
    project_root = Path(__file__).resolve().parents[3]
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)


async def _persist_telegram_runtime(client, *, phone: str | None) -> None:
    from data.ingest.telegram.config import delete_legacy_session_file, get_telegram_settings, persist_telegram_env

    settings = get_telegram_settings()
    updates = {
        "TELEGRAM_SESSION_STRING": client.export_session_string(),
    }
    if phone:
        updates["TELEGRAM_PHONE"] = phone

    persist_telegram_env(updates)
    delete_legacy_session_file(settings)
