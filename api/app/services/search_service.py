from sqlalchemy import func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from .entities_service import _display_names_subquery


async def search(
    session: AsyncSession,
    *,
    q: str,
    limit: int,
    offset: int,
) -> tuple[list[dict], str]:
    indexed_count = 0
    has_search_documents = await session.scalar(
        text("select to_regclass(current_schema() || '.search_documents') is not null")
    )
    if has_search_documents:
        indexed_count = await session.scalar(
            select(func.count()).select_from(models.search_documents)
        )
    if indexed_count:
        return await _search_documents(session, q=q, limit=limit, offset=offset), "search_documents"
    return await _search_entity_names(session, q=q, limit=limit, offset=offset), "entity_names"


async def _search_documents(
    session: AsyncSession,
    *,
    q: str,
    limit: int,
    offset: int,
) -> list[dict]:
    display_names = _display_names_subquery()
    ts_query = func.websearch_to_tsquery("simple", q)
    rank = func.ts_rank_cd(models.search_documents.c.content_tsv, ts_query)
    pattern = f"%{q}%"
    stmt = (
        select(
            literal("entity").label("kind"),
            models.search_documents.c.id,
            models.search_documents.c.entity_id,
            display_names.c.primary_name.label("title"),
            func.substring(models.search_documents.c.content, 1, 240).label("snippet"),
            rank.label("score"),
        )
        .select_from(
            models.search_documents.outerjoin(
                display_names,
                display_names.c.entity_id == models.search_documents.c.entity_id,
            )
        )
        .where(
            or_(
                models.search_documents.c.content_tsv.op("@@")(ts_query),
                models.search_documents.c.content.ilike(pattern),
            )
        )
        .order_by(rank.desc().nullslast(), models.search_documents.c.id)
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings().all()]


async def _search_entity_names(
    session: AsyncSession,
    *,
    q: str,
    limit: int,
    offset: int,
) -> list[dict]:
    pattern = f"%{q}%"
    stmt = (
        select(
            literal("entity").label("kind"),
            models.entity_names.c.id,
            models.entity_names.c.entity_id,
            models.entity_names.c.name.label("title"),
            models.entities.c.description.label("snippet"),
            literal(None).label("score"),
        )
        .select_from(
            models.entity_names.join(
                models.entities,
                models.entities.c.id == models.entity_names.c.entity_id,
            )
        )
        .where(
            models.entity_names.c.name.ilike(pattern),
            models.entities.c.status == "active",
        )
        .order_by(models.entity_names.c.name, models.entity_names.c.id)
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings().all()]
