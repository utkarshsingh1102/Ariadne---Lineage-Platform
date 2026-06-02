"""Async Postgres pool for the /tws/jobs endpoint."""
from __future__ import annotations

import asyncpg

from .config import Settings

_pool: asyncpg.Pool | None = None


async def init_pool(settings: Settings) -> None:
    global _pool
    try:
        _pool = await asyncpg.create_pool(
            host=settings.postgres_host,
            port=settings.postgres_port,
            user=settings.postgres_user,
            password=settings.postgres_password,
            database=settings.postgres_db,
            min_size=1,
            max_size=5,
            command_timeout=10,
        )
    except Exception:
        # Postgres may be down during tests / partial-stack runs — degrade gracefully.
        _pool = None


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool | None:
    return _pool
