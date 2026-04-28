from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models


async def list_review_queue(
    session: AsyncSession,
    *,
    status: str | None,
    risk_level: str | None,
    item_kind: str | None,
    limit: int,
    offset: int,
) -> list[dict]:
    stmt = (
        select(
            models.review_queue.c.id,
            models.review_queue.c.item_kind,
            models.review_queue.c.item_id,
            models.review_queue.c.risk_level,
            models.review_queue.c.status,
            models.review_queue.c.reason,
            models.review_queue.c.priority_score,
            models.review_queue.c.created_at,
            models.review_queue.c.resolved_at,
            models.review_queue.c.resolved_by,
        )
        .order_by(
            models.review_queue.c.priority_score.desc().nullslast(),
            models.review_queue.c.created_at,
            models.review_queue.c.id,
        )
        .limit(limit)
        .offset(offset)
    )
    if status:
        stmt = stmt.where(models.review_queue.c.status == status)
    if risk_level:
        stmt = stmt.where(models.review_queue.c.risk_level == risk_level)
    if item_kind:
        stmt = stmt.where(models.review_queue.c.item_kind == item_kind)

    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings().all()]
