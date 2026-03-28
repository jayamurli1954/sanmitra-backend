import asyncio
from collections.abc import AsyncGenerator
from typing import Tuple

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine(uri: str) -> AsyncEngine:
    settings = get_settings()
    connect_args: dict[str, int] = {}
    if uri.startswith("postgresql+asyncpg"):
        connect_args["timeout"] = settings.PG_CONNECT_TIMEOUT_SECONDS

    kwargs: dict[str, object] = {
        "pool_pre_ping": True,
        "pool_size": max(1, settings.PG_POOL_SIZE),
        "max_overflow": max(0, settings.PG_MAX_OVERFLOW),
        "pool_timeout": max(1, settings.PG_POOL_TIMEOUT_SECONDS),
        "pool_recycle": max(30, settings.PG_POOL_RECYCLE_SECONDS),
        "pool_use_lifo": True,
    }
    if connect_args:
        kwargs["connect_args"] = connect_args

    return create_async_engine(uri, **kwargs)


async def init_postgres() -> None:
    global _engine, _session_factory

    settings = get_settings()
    if not settings.POSTGRES_URI:
        _engine = None
        _session_factory = None
        return

    if _engine is None:
        _engine = _build_engine(settings.POSTGRES_URI)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def close_postgres() -> None:
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("PostgreSQL is not initialized")
    return _session_factory


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


async def create_postgres_tables(metadata: sa.MetaData) -> None:
    if _engine is None:
        return
    async with _engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


async def ping_postgres() -> Tuple[bool, str]:
    settings = get_settings()
    if not settings.POSTGRES_URI:
        return False, "not_configured"

    try:
        await init_postgres()
        if _engine is None:
            return False, "not_initialized"

        async def _ping_once() -> None:
            async with _engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))

        await asyncio.wait_for(_ping_once(), timeout=max(1, settings.PG_CONNECT_TIMEOUT_SECONDS + 1))
        return True, "ok"
    except asyncio.TimeoutError:
        return False, "timeout"
    except Exception as exc:
        return False, str(exc)

