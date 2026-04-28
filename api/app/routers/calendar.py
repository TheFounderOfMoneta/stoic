from datetime import date, datetime, timedelta, timezone as datetime_timezone
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..schemas import CalendarEventsResponse
from ..services.calendar_service import list_calendar_events


router = APIRouter(prefix="/calendar", tags=["calendar"])

TIMEZONE_FALLBACKS = {
    "Europe/Moscow": datetime_timezone(timedelta(hours=3), "Europe/Moscow"),
}


def _resolve_timezone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        fallback = TIMEZONE_FALLBACKS.get(timezone_name)
        if fallback:
            return fallback
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unknown timezone.",
        ) from exc


@router.get("/events", response_model=CalendarEventsResponse)
async def calendar_events(
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
    timezone: Annotated[str, Query(min_length=1)] = "Europe/Moscow",
    session: AsyncSession = Depends(get_session),
) -> CalendarEventsResponse:
    if date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date_to must be greater than or equal to date_from.",
        )
    zone = _resolve_timezone(timezone)
    items = await list_calendar_events(
        session,
        date_from=date_from,
        date_to=date_to,
        timezone=zone,
    )
    return CalendarEventsResponse(
        items=items,
        date_from=date_from,
        date_to=date_to,
        timezone=timezone,
    )


@router.get("/events/today", response_model=CalendarEventsResponse)
async def calendar_events_today(
    timezone: Annotated[str, Query(min_length=1)] = "Europe/Moscow",
    session: AsyncSession = Depends(get_session),
) -> CalendarEventsResponse:
    zone = _resolve_timezone(timezone)
    today = datetime.now(zone).date()
    items = await list_calendar_events(
        session,
        date_from=today,
        date_to=today,
        timezone=zone,
    )
    return CalendarEventsResponse(
        items=items,
        date_from=today,
        date_to=today,
        timezone=timezone,
    )
