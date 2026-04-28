from sqlalchemy import case, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models


def _display_names_subquery(name: str = "display_names"):
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
        .subquery(name)
    )


async def list_entities(
    session: AsyncSession,
    *,
    type_id: str | None,
    status: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> list[dict]:
    display_names = _display_names_subquery()
    stmt = (
        select(
            models.entities.c.id,
            models.entities.c.type_id,
            models.entities.c.status,
            models.entities.c.description,
            models.entities.c.created_at,
            models.entities.c.updated_at,
            models.entities.c.merged_into_entity_id,
            display_names.c.primary_name,
        )
        .select_from(
            models.entities.outerjoin(
                display_names,
                display_names.c.entity_id == models.entities.c.id,
            )
        )
        .order_by(models.entities.c.updated_at.desc(), models.entities.c.id)
        .limit(limit)
        .offset(offset)
    )

    if type_id:
        stmt = stmt.where(models.entities.c.type_id == type_id)
    if status:
        stmt = stmt.where(models.entities.c.status == status)
    if q:
        pattern = f"%{q}%"
        name_exists = exists(
            select(models.entity_names.c.id).where(
                models.entity_names.c.entity_id == models.entities.c.id,
                models.entity_names.c.name.ilike(pattern),
            )
        )
        stmt = stmt.where(name_exists)

    result = await session.execute(stmt)
    return [dict(row) for row in result.mappings().all()]


async def get_entity_card(session: AsyncSession, entity_id: str) -> dict | None:
    display_names = _display_names_subquery()
    entity_stmt = (
        select(
            models.entities.c.id,
            models.entities.c.type_id,
            models.entities.c.status,
            models.entities.c.description,
            models.entities.c.created_at,
            models.entities.c.updated_at,
            models.entities.c.merged_into_entity_id,
            display_names.c.primary_name,
        )
        .select_from(
            models.entities.outerjoin(
                display_names,
                display_names.c.entity_id == models.entities.c.id,
            )
        )
        .where(models.entities.c.id == entity_id)
    )
    entity = (await session.execute(entity_stmt)).mappings().first()
    if not entity:
        return None

    names_stmt = (
        select(
            models.entity_names.c.id,
            models.entity_names.c.name,
            models.entity_names.c.kind,
            models.entity_names.c.language,
            models.entity_names.c.source_id,
            models.entity_names.c.created_at,
        )
        .where(models.entity_names.c.entity_id == entity_id)
        .order_by(models.entity_names.c.kind, models.entity_names.c.name)
    )
    names = [dict(row) for row in (await session.execute(names_stmt)).mappings().all()]

    facts_stmt = (
        select(
            models.facts.c.id,
            models.facts.c.series_id,
            models.facts.c.fact_type_id,
            models.fact_types.c.name.label("fact_type_name"),
            models.fact_types.c.value_kind,
            models.facts.c.value_json,
            models.facts.c.status,
            models.facts.c.confidence,
            models.facts.c.valid_from,
            models.facts.c.valid_to,
            models.facts.c.recorded_from,
            models.facts.c.recorded_to,
            models.facts.c.last_verified_at,
            models.facts.c.primary_source_fragment_id,
            models.facts.c.created_by,
        )
        .select_from(
            models.facts.join(
                models.fact_types,
                models.fact_types.c.id == models.facts.c.fact_type_id,
            )
        )
        .where(
            models.facts.c.entity_id == entity_id,
            models.facts.c.status == "active",
            models.facts.c.recorded_to.is_(None),
        )
        .order_by(models.facts.c.recorded_from.desc(), models.facts.c.id)
    )
    facts = [dict(row) for row in (await session.execute(facts_stmt)).mappings().all()]

    relation_names = _display_names_subquery("relation_display_names")
    from_names = relation_names.alias("from_names")
    to_names = relation_names.alias("to_names")
    relations_stmt = (
        select(
            models.relations.c.id,
            models.relations.c.series_id,
            models.relations.c.relation_type_id,
            models.relation_types.c.name.label("relation_type_name"),
            models.relation_types.c.directed,
            case(
                (models.relations.c.from_entity_id == entity_id, literal("outgoing")),
                else_=literal("incoming"),
            ).label("direction"),
            models.relations.c.from_entity_id,
            from_names.c.primary_name.label("from_entity_name"),
            models.relations.c.to_entity_id,
            to_names.c.primary_name.label("to_entity_name"),
            models.relations.c.status,
            models.relations.c.confidence,
            models.relations.c.valid_from,
            models.relations.c.valid_to,
            models.relations.c.recorded_from,
            models.relations.c.recorded_to,
            models.relations.c.last_verified_at,
            models.relations.c.primary_source_fragment_id,
            models.relations.c.created_by,
        )
        .select_from(
            models.relations.join(
                models.relation_types,
                models.relation_types.c.id == models.relations.c.relation_type_id,
            )
            .outerjoin(
                from_names,
                from_names.c.entity_id == models.relations.c.from_entity_id,
            )
            .outerjoin(
                to_names,
                to_names.c.entity_id == models.relations.c.to_entity_id,
            )
        )
        .where(
            or_(
                models.relations.c.from_entity_id == entity_id,
                models.relations.c.to_entity_id == entity_id,
            ),
            models.relations.c.status == "active",
            models.relations.c.recorded_to.is_(None),
        )
        .order_by(models.relations.c.recorded_from.desc(), models.relations.c.id)
    )
    relations = [
        dict(row) for row in (await session.execute(relations_stmt)).mappings().all()
    ]

    return {
        **dict(entity),
        "names": names,
        "facts": facts,
        "relations": relations,
    }
