from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..schemas import MetaResponse
from ..services.meta_service import get_meta


router = APIRouter(tags=["meta"])


@router.get("/meta", response_model=MetaResponse)
async def meta(session: AsyncSession = Depends(get_session)) -> dict:
    return await get_meta(session)
