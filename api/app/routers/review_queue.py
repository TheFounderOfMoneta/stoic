from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..schemas import ReviewItemKind, ReviewQueueResponse, ReviewStatus, RiskLevel
from ..services.review_service import list_review_queue


router = APIRouter(prefix="/review-queue", tags=["review-queue"])


@router.get("", response_model=ReviewQueueResponse)
async def review_queue(
    status_filter: Annotated[ReviewStatus | None, Query(alias="status")] = "queued",
    risk_level: Annotated[RiskLevel | None, Query()] = None,
    item_kind: Annotated[ReviewItemKind | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_session),
) -> dict:
    items = await list_review_queue(
        session,
        status=status_filter,
        risk_level=risk_level,
        item_kind=item_kind,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "limit": limit, "offset": offset}
