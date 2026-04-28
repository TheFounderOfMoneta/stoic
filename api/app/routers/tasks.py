from datetime import date, datetime, timedelta, timezone as datetime_timezone
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..schemas import TaskBacklogResponse, TaskDeadlineCountResponse, TaskDeadlineListResponse
from ..services.tasks_service import (
    count_tasks_with_deadline_on,
    list_tasks_with_deadline_on,
    list_tasks_without_deadline,
)


router = APIRouter(prefix="/tasks", tags=["tasks"])

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


@router.get("/deadlines/today/count", response_model=TaskDeadlineCountResponse)
async def tasks_deadline_today_count(
    timezone: Annotated[str, Query(min_length=1)] = "Europe/Moscow",
    target_date: Annotated[date | None, Query(alias="date")] = None,
    session: AsyncSession = Depends(get_session),
) -> TaskDeadlineCountResponse:
    zone = _resolve_timezone(timezone)
    resolved_date = target_date or datetime.now(zone).date()
    count = await count_tasks_with_deadline_on(
        session,
        target_date=resolved_date,
        timezone=timezone,
    )
    return TaskDeadlineCountResponse(
        count=count,
        date=resolved_date,
        timezone=timezone,
    )


@router.get("/deadlines/today", response_model=TaskDeadlineListResponse)
async def tasks_deadline_today(
    timezone: Annotated[str, Query(min_length=1)] = "Europe/Moscow",
    target_date: Annotated[date | None, Query(alias="date")] = None,
    session: AsyncSession = Depends(get_session),
) -> TaskDeadlineListResponse:
    zone = _resolve_timezone(timezone)
    resolved_date = target_date or datetime.now(zone).date()
    items = await list_tasks_with_deadline_on(
        session,
        target_date=resolved_date,
        timezone=timezone,
    )
    return TaskDeadlineListResponse(
        items=items,
        date=resolved_date,
        timezone=timezone,
    )


@router.get("/backlog", response_model=TaskBacklogResponse)
async def tasks_without_deadline(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_session),
) -> TaskBacklogResponse:
    items = await list_tasks_without_deadline(
        session,
        limit=limit,
        offset=offset,
    )
    return TaskBacklogResponse(
        items=items,
        limit=limit,
        offset=offset,
    )
