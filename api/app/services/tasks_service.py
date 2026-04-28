from datetime import date

from sqlalchemy import Date, DateTime, cast, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models


def _display_names_subquery():
    return (
        select(
            models.entity_names.c.entity_id,
            func.coalesce(
                func.min(models.entity_names.c.name).filter(
                    models.entity_names.c.kind == "primary"
                ),
                func.min(models.entity_names.c.name),
            ).label("primary_name"),
        )
        .group_by(models.entity_names.c.entity_id)
        .subquery("task_display_names")
    )


def _deadline_expressions(timezone: str):
    deadline_at = func.timezone(
        timezone,
        cast(models.facts.c.value_json["value"].astext, DateTime(timezone=True)),
    ).label("deadline_at")
    deadline_date = cast(deadline_at, Date)
    return deadline_at, deadline_date


async def list_tasks_with_deadline_on(
    session: AsyncSession,
    *,
    target_date: date,
    timezone: str,
) -> list[dict]:
    display_names = _display_names_subquery()
    deadline_at, deadline_date = _deadline_expressions(timezone)
    stmt = (
        select(
            models.entities.c.id,
            func.coalesce(
                display_names.c.primary_name,
                models.entities.c.description,
                models.entities.c.id,
            ).label("text"),
            deadline_at,
        )
        .select_from(
            models.entities.join(
                models.facts,
                models.facts.c.entity_id == models.entities.c.id,
            ).outerjoin(
                display_names,
                display_names.c.entity_id == models.entities.c.id,
            )
        )
        .where(
            models.entities.c.type_id == "task",
            models.entities.c.status == "active",
            models.facts.c.fact_type_id == "deadline",
            models.facts.c.status == "active",
            models.facts.c.recorded_to.is_(None),
            deadline_date == target_date,
        )
        .order_by(deadline_at, models.entities.c.id)
    )
    rows = [dict(row) for row in (await session.execute(stmt)).mappings().all()]
    return [
        {
            "id": row["id"],
            "text": row["text"],
            "deadline_at": row["deadline_at"],
            "date": target_date,
            "time": row["deadline_at"].strftime("%H:%M"),
            "done": False,
        }
        for row in rows
    ]


async def count_tasks_with_deadline_on(
    session: AsyncSession,
    *,
    target_date: date,
    timezone: str,
) -> int:
    _, deadline_date = _deadline_expressions(timezone)
    stmt = (
        select(func.count())
        .select_from(
            models.entities.join(
                models.facts,
                models.facts.c.entity_id == models.entities.c.id,
            )
        )
        .where(
            models.entities.c.type_id == "task",
            models.entities.c.status == "active",
            models.facts.c.fact_type_id == "deadline",
            models.facts.c.status == "active",
            models.facts.c.recorded_to.is_(None),
            deadline_date == target_date,
        )
    )
    return int(await session.scalar(stmt) or 0)


async def list_tasks_without_deadline(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
) -> list[dict]:
    display_names = _display_names_subquery()
    active_deadline_exists = exists(
        select(models.facts.c.id).where(
            models.facts.c.entity_id == models.entities.c.id,
            models.facts.c.fact_type_id == "deadline",
            models.facts.c.status == "active",
            models.facts.c.recorded_to.is_(None),
        )
    )
    stmt = (
        select(
            models.entities.c.id,
            func.coalesce(
                display_names.c.primary_name,
                models.entities.c.description,
                models.entities.c.id,
            ).label("text"),
            models.entities.c.updated_at,
        )
        .select_from(
            models.entities.outerjoin(
                display_names,
                display_names.c.entity_id == models.entities.c.id,
            )
        )
        .where(
            models.entities.c.type_id == "task",
            models.entities.c.status == "active",
            ~active_deadline_exists,
        )
        .order_by(models.entities.c.updated_at.desc(), models.entities.c.id)
        .limit(limit)
        .offset(offset)
    )
    return [dict(row) for row in (await session.execute(stmt)).mappings().all()]
