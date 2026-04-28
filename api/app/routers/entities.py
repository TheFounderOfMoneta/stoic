from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..schemas import EntityCardResponse, EntityListResponse, EntityStatus
from ..services.entities_service import get_entity_card, list_entities


router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("", response_model=EntityListResponse)
async def entities(
    type_id: Annotated[str | None, Query(min_length=1)] = None,
    status_filter: Annotated[EntityStatus | None, Query(alias="status")] = "active",
    q: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_session),
) -> dict:
    items = await list_entities(
        session,
        type_id=type_id,
        status=status_filter,
        q=q,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/{entity_id}", response_model=EntityCardResponse)
async def entity_detail(
    entity_id: Annotated[str, Path(min_length=1)],
    session: AsyncSession = Depends(get_session),
) -> dict:
    entity = await get_entity_card(session, entity_id)
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entity not found.",
        )
    return entity
