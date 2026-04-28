from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models


async def get_meta(session: AsyncSession) -> dict:
    entity_types_result = await session.execute(
        select(models.entity_types).order_by(models.entity_types.c.id)
    )
    fact_types_result = await session.execute(
        select(models.fact_types).order_by(models.fact_types.c.id)
    )
    relation_types_result = await session.execute(
        select(models.relation_types).order_by(models.relation_types.c.id)
    )

    return {
        "entity_types": [dict(row) for row in entity_types_result.mappings().all()],
        "fact_types": [dict(row) for row in fact_types_result.mappings().all()],
        "relation_types": [dict(row) for row in relation_types_result.mappings().all()],
    }
