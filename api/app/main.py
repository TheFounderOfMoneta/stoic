from collections.abc import AsyncIterator
import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import engine
from .responses import UTF8JSONResponse
from .routers import (
    calendar,
    codex,
    entities,
    health,
    incoming_sources,
    messages,
    meta,
    review_queue,
    search,
    source_fragments,
    tasks,
)
from .security import require_api_key
from .services.codex_service import codex_update_check_loop
from .services.telegram_realtime_service import telegram_updates_loop


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    codex_update_task = asyncio.create_task(codex_update_check_loop(settings))
    telegram_updates_task = asyncio.create_task(telegram_updates_loop())
    try:
        yield
    finally:
        codex_update_task.cancel()
        telegram_updates_task.cancel()
        with suppress(asyncio.CancelledError):
            await codex_update_task
        with suppress(asyncio.CancelledError):
            await telegram_updates_task
        await engine.dispose()


app = FastAPI(
    title="Stoic API",
    version="0.1.0",
    description="Read-only REST API for Stoic knowledge storage.",
    lifespan=lifespan,
    default_response_class=UTF8JSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["X-API-Key", "Content-Type"],
)

app.include_router(health.router, prefix="/api/v1")

protected_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_key)],
)
protected_router.include_router(meta.router)
protected_router.include_router(entities.router)
protected_router.include_router(search.router)
protected_router.include_router(incoming_sources.router)
protected_router.include_router(source_fragments.router)
protected_router.include_router(review_queue.router)
protected_router.include_router(tasks.router)
protected_router.include_router(calendar.router)
protected_router.include_router(codex.router)
protected_router.include_router(messages.router)

app.include_router(protected_router)
