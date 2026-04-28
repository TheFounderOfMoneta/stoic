from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings


def _async_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


settings = get_settings()
engine = create_async_engine(
    _async_database_url(settings.database_url),
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def _prepare_session(session: AsyncSession, *, read_only: bool) -> None:
    await session.execute(text(f"SET search_path TO {settings.database_schema}, public"))
    if read_only:
        await session.execute(text("SET TRANSACTION READ ONLY"))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        async with session.begin():
            await _prepare_session(session, read_only=True)
            yield session


async def get_write_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        async with session.begin():
            await _prepare_session(session, read_only=False)
            yield session
