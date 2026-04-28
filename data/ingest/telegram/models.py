from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TelegramDialogSummary(BaseModel):
    id: int
    title: str
    entity_type: str
    kind: str
    username: str | None = None
    unread_count: int = 0
    message_count_hint: int | None = None
    pinned: bool = False
    position: int = 0
    last_message_at: datetime | None = None
    last_message_text: str | None = None


class TelegramMessageRecord(BaseModel):
    id: int
    chat_id: int
    chat_title: str
    sender_id: int | None = None
    sender_name: str | None = None
    text: str | None = None
    outgoing: bool = False
    sent_at: datetime
    raw: dict[str, Any]
