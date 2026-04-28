from __future__ import annotations

from typing import Any

from .models import TelegramMessageRecord


def telegram_message_to_source(message: TelegramMessageRecord) -> dict[str, Any]:
    raw_text = (message.text or "").strip()
    return {
        "source_type": "chat_message",
        "origin": "telegram",
        "url_or_path": None,
        "captured_at": message.sent_at,
        "status": "captured",
        "raw_text": raw_text,
        "raw_blob_ref": None,
        "metadata_json": {
            "telegram": {
                "chat_id": message.chat_id,
                "chat_title": message.chat_title,
                "message_id": message.id,
                "sender_id": message.sender_id,
                "sender_name": message.sender_name,
            },
            "raw": message.raw,
        },
    }
