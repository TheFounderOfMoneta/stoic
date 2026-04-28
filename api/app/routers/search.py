from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..schemas import SearchResponse
from ..services.search_service import search


router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search_endpoint(
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_session),
) -> dict:
    items, source = await search(session, q=q, limit=limit, offset=offset)
    return {"items": items, "limit": limit, "offset": offset, "source": source}
