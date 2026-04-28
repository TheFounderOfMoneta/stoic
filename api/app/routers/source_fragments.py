from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..schemas import SourceFragmentDetail
from ..services.sources_service import get_source_fragment


router = APIRouter(prefix="/source-fragments", tags=["source-fragments"])


@router.get("/{fragment_id}", response_model=SourceFragmentDetail)
async def source_fragment_detail(
    fragment_id: Annotated[str, Path(min_length=1)],
    session: AsyncSession = Depends(get_session),
) -> dict:
    fragment = await get_source_fragment(session, fragment_id)
    if not fragment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source fragment not found.",
        )
    return fragment
