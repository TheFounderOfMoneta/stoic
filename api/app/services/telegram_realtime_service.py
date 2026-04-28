from __future__ import annotations

import asyncio
from datetime import UTC
from contextlib import suppress

from telethon import events

from ..db import SessionLocal, _prepare_session
from .messages_service import _build_telegram_client, _existing_telegram_message_ids, _upsert_telegram_dialog_account
from .. import models
from sqlalchemy import insert


def _normalize_datetime(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _sender_name(message) -> str | None:
    sender = getattr(message, "sender", None)
    if sender is None:
        return None
    first_name = getattr(sender, "first_name", None) or ""
    last_name = getattr(sender, "last_name", None) or ""
    username = getattr(sender, "username", None)
    full_name = f"{first_name} {last_name}".strip()
    return full_name or username


async def telegram_updates_loop() -> None:
    while True:
        client = await _build_telegram_client()
        sync_task: asyncio.Task[None] | None = None
        try:
            await client.connect()
            if not await client.is_authorized():
                await client.disconnect()
                await asyncio.sleep(15)
                continue

            await _sync_dialogs_with_live_client(client)
            sync_task = asyncio.create_task(_periodic_dialog_sync(client))

            @client.raw_client.on(events.NewMessage)
            async def on_new_message(event):
                message = event.message
                chat = await event.get_chat()
                title = getattr(chat, "title", None) or getattr(chat, "username", None) or str(getattr(chat, "id", "telegram"))
                entity = getattr(chat, "username", None) or str(getattr(chat, "id", "telegram"))
                chat_id = getattr(message.peer_id, "channel_id", None) or getattr(message.peer_id, "chat_id", None) or getattr(message.peer_id, "user_id", 0)
                sender_name = _sender_name(message)
                sent_at = _normalize_datetime(message.date)
                text = (message.message or "").strip()
                async with SessionLocal() as session:
                    async with session.begin():
                        await _prepare_session(session, read_only=False)
                        existing = await _existing_telegram_message_ids(
                            session,
                            chat_id=str(chat_id),
                            message_ids=[str(message.id)],
                        )
                        if str(message.id) in existing:
                            return

                        sender_role = "user" if bool(getattr(message, "out", False)) else "contact"
                        await _upsert_telegram_dialog_account(
                            session,
                            chat_id=int(chat_id),
                            title=title,
                            entity=entity,
                            entity_type=chat.__class__.__name__,
                            kind="group" if getattr(chat, "megagroup", False) or chat.__class__.__name__ in {"Chat", "Channel"} else "contact",
                            username=getattr(chat, "username", None),
                            unread_count=0,
                            message_count_hint=None,
                            telegram_pinned=None,
                            position=None,
                            last_message_at=sent_at,
                            last_message_text=text,
                        )
                        await session.execute(
                            insert(models.sources).values(
                                id=f"telegram-live:{chat_id}:{message.id}",
                                source_type="chat_message",
                                origin="telegram",
                                source_account_id="telegram:primary",
                                url_or_path=None,
                                captured_at=sent_at,
                                status="captured",
                                raw_text=text,
                                raw_blob_ref=None,
                                metadata_json={
                                    "channel": "telegram",
                                    "sender_role": sender_role,
                                    "sender_name": sender_name,
                                    "conversation": {
                                        "id": f"telegram:{chat_id}",
                                        "title": title,
                                    },
                                    "telegram": {
                                        "chat_id": chat_id,
                                        "chat_title": title,
                                        "message_id": message.id,
                                        "sender_id": message.sender_id,
                                        "sender_name": sender_name,
                                    },
                                    "raw": message.to_dict(),
                                },
                            )
                        )

            await client.raw_client.run_until_disconnected()
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(10)
        finally:
            if sync_task is not None:
                sync_task.cancel()
                with suppress(asyncio.CancelledError):
                    await sync_task
            try:
                await client.disconnect()
            except Exception:
                pass


async def _sync_dialogs_with_live_client(client) -> None:
    dialogs = await client.list_dialogs(limit=500)
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


async def _periodic_dialog_sync(client) -> None:
    while True:
        await asyncio.sleep(180)
        await _sync_dialogs_with_live_client(client)
