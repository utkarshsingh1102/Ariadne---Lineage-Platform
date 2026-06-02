"""SQLAlchemy engine + session helpers."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text

from tws_parser.config import settings


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(settings.postgres_dsn, pool_pre_ping=True, future=True)
    return _engine


def close() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def healthcheck() -> bool:
    try:
        with get_engine().begin() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
