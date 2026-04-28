from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..schemas import (
    FragmentType,
    IncomingSourceDetail,
    IncomingSourceListResponse,
    SourceFragmentListResponse,
    SourceOrigin,
    SourceStatus,
    SourceType,
)
from ..services.sources_service import (
    get_source_detail,
    list_source_fragments,
    list_sources,
    source_exists,
)


router = APIRouter(prefix="/incoming-sources", tags=["incoming-sources"])


@router.get("", response_model=IncomingSourceListResponse)
async def incoming_sources(
    source_type: Annotated[SourceType | None, Query()] = None,
    origin: Annotated[SourceOrigin | None, Query()] = None,
    status_filter: Annotated[SourceStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_session),
) -> dict:
    items = await list_sources(
        session,
        source_type=source_type,
        origin=origin,
        status=status_filter,
        q=q,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/{source_id}", response_model=IncomingSourceDetail)
async def incoming_source_detail(
    source_id: Annotated[str, Path(min_length=1)],
    session: AsyncSession = Depends(get_session),
) -> dict:
    source = await get_source_detail(session, source_id)
    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incoming source not found.",
        )
    return source


@router.get("/{source_id}/fragments", response_model=SourceFragmentListResponse)
async def incoming_source_fragments(
    source_id: Annotated[str, Path(min_length=1)],
    fragment_type: Annotated[FragmentType | None, Query()] = None,
    q: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not await source_exists(session, source_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incoming source not found.",
        )
    items = await list_source_fragments(
        session,
        source_id=source_id,
        fragment_type=fragment_type,
        q=q,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "limit": limit, "offset": offset}
