from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models


DEFAULT_EVENT_COLOR = "#4B78F5"
COLOR_PALETTE = ["#4B78F5", "#6C5CE7", "#00B894", "#FD9644", "#E17055", "#A29BFE"]
START_KEYS = ("starts_at", "start_at", "start", "date_start", "scheduled_at")
END_KEYS = ("ends_at", "end_at", "end", "date_end")
TITLE_KEYS = ("title", "summary", "name")
LOCATION_KEYS = ("location", "where", "place")


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _color_from_seed(seed: str) -> str:
    return COLOR_PALETTE[sum(ord(char) for char in seed) % len(COLOR_PALETTE)]


def _pick_first(metadata: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in metadata and metadata[key] not in (None, ""):
            return metadata[key]
    return None


def _event_from_source(row: dict[str, Any], timezone: ZoneInfo) -> dict[str, Any] | None:
    metadata = row.get("metadata_json")
    if not isinstance(metadata, dict):
        metadata = {}

    starts_at = _parse_datetime(_pick_first(metadata, START_KEYS))
    if starts_at is None:
        return None
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone)
    else:
        starts_at = starts_at.astimezone(timezone)

    ends_at = _parse_datetime(_pick_first(metadata, END_KEYS))
    if ends_at is not None:
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=timezone)
        else:
            ends_at = ends_at.astimezone(timezone)

    title = _pick_first(metadata, TITLE_KEYS) or row.get("raw_text") or row["id"]
    location = _pick_first(metadata, LOCATION_KEYS)
    color = metadata.get("color") if isinstance(metadata.get("color"), str) else None
    return {
        "id": row["id"],
        "title": str(title),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "date": starts_at.date(),
        "time": starts_at.strftime("%H:%M"),
        "color": color or _color_from_seed(str(title)),
        "location": str(location) if location else None,
    }


async def list_calendar_events(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    timezone: ZoneInfo,
) -> list[dict]:
    stmt = (
        select(
            models.sources.c.id,
            models.sources.c.raw_text,
            models.sources.c.metadata_json,
        )
        .where(
            models.sources.c.source_type == "calendar_event",
            models.sources.c.status != "error",
        )
        .order_by(models.sources.c.captured_at, models.sources.c.id)
    )
    rows = [dict(row) for row in (await session.execute(stmt)).mappings().all()]
    items: list[dict] = []
    for row in rows:
        event = _event_from_source(row, timezone)
        if event and date_from <= event["date"] <= date_to:
            items.append(event)
    return items
