from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models


def _source_summary_columns():
    return (
        models.sources.c.id,
        models.sources.c.source_type,
        models.sources.c.origin,
        models.sources.c.source_account_id,
        models.sources.c.url_or_path,
        models.sources.c.captured_at,
        models.sources.c.status,
        models.sources.c.raw_blob_ref,
        models.sources.c.metadata_json,
        func.substring(models.sources.c.raw_text, 1, 240).label("raw_text_preview"),
    )


async def list_sources(
    session: AsyncSession,
    *,
    source_type: str | None,
    origin: str | None,
    status: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> list[dict]:
    stmt = (
        select(*_source_summary_columns())
        .order_by(models.sources.c.captured_at.desc(), models.sources.c.id)
        .limit(limit)
        .offset(offset)
    )
    if source_type:
        stmt = stmt.where(models.sources.c.source_type == source_type)
    if origin:
        stmt = stmt.where(models.sources.c.origin == origin)
    if status:
        stmt = stmt.where(models.sources.c.status == status)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                models.sources.c.raw_text.ilike(pattern),
                models.sources.c.url_or_path.ilike(pattern),
            )
        )

    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings().all()]


async def get_source_detail(session: AsyncSession, source_id: str) -> dict | None:
    source_stmt = select(
        models.sources.c.id,
        models.sources.c.source_type,
        models.sources.c.origin,
        models.sources.c.source_account_id,
        models.sources.c.url_or_path,
        models.sources.c.captured_at,
        models.sources.c.status,
        models.sources.c.raw_blob_ref,
        models.sources.c.metadata_json,
        func.substring(models.sources.c.raw_text, 1, 240).label("raw_text_preview"),
        models.sources.c.raw_text,
    ).where(models.sources.c.id == source_id)
    source = (await session.execute(source_stmt)).mappings().first()
    if not source:
        return None

    fragments_count = await session.scalar(
        select(func.count()).select_from(models.source_fragments).where(
            models.source_fragments.c.source_id == source_id
        )
    )
    attachments_stmt = (
        select(
            models.attachments.c.id,
            models.attachments.c.source_id,
            models.attachments.c.path,
            models.attachments.c.mime_type,
            models.attachments.c.size_bytes,
            models.attachments.c.checksum_sha256,
            models.attachments.c.auto_summary,
            models.attachments.c.manual_title,
            models.attachments.c.manual_why_saved,
            models.attachments.c.manual_how_to_find,
            models.attachments.c.manual_main_point,
            models.attachments.c.manual_keywords,
            models.attachments.c.manual_review_after,
            models.attachments.c.created_at,
        )
        .where(models.attachments.c.source_id == source_id)
        .order_by(models.attachments.c.created_at.desc(), models.attachments.c.id)
    )
    attachments = [
        dict(row) for row in (await session.execute(attachments_stmt)).mappings().all()
    ]

    return {
        **dict(source),
        "fragments_count": int(fragments_count or 0),
        "attachments_count": len(attachments),
        "attachments": attachments,
    }


async def source_exists(session: AsyncSession, source_id: str) -> bool:
    exists_stmt = select(models.sources.c.id).where(models.sources.c.id == source_id).limit(1)
    return (await session.scalar(exists_stmt)) is not None


async def list_source_fragments(
    session: AsyncSession,
    *,
    source_id: str,
    fragment_type: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> list[dict]:
    stmt = (
        select(
            models.source_fragments.c.id,
            models.source_fragments.c.source_id,
            models.source_fragments.c.locator,
            models.source_fragments.c.text,
            models.source_fragments.c.normalized_text,
            models.source_fragments.c.fragment_type,
            models.source_fragments.c.embedding_ref,
            models.source_fragments.c.created_at,
        )
        .where(models.source_fragments.c.source_id == source_id)
        .order_by(models.source_fragments.c.created_at, models.source_fragments.c.id)
        .limit(limit)
        .offset(offset)
    )
    if fragment_type:
        stmt = stmt.where(models.source_fragments.c.fragment_type == fragment_type)
    if q:
        stmt = stmt.where(models.source_fragments.c.text.ilike(f"%{q}%"))

    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings().all()]


async def get_source_fragment(session: AsyncSession, fragment_id: str) -> dict | None:
    fragment_stmt = select(
        models.source_fragments.c.id,
        models.source_fragments.c.source_id,
        models.source_fragments.c.locator,
        models.source_fragments.c.text,
        models.source_fragments.c.normalized_text,
        models.source_fragments.c.fragment_type,
        models.source_fragments.c.embedding_ref,
        models.source_fragments.c.created_at,
    ).where(models.source_fragments.c.id == fragment_id)
    fragment = (await session.execute(fragment_stmt)).mappings().first()
    if not fragment:
        return None

    source_stmt = select(*_source_summary_columns()).where(
        models.sources.c.id == fragment["source_id"]
    )
    source = (await session.execute(source_stmt)).mappings().first()
    return {
        **dict(fragment),
        "source": dict(source) if source else None,
    }
