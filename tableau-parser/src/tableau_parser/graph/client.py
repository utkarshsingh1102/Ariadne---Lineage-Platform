"""Thin wrapper around the Neo4j Bolt driver."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from neo4j import Driver, GraphDatabase, Session

from tableau_parser.config import settings


_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
    return _driver


@contextmanager
def session(database: str | None = None) -> Iterator[Session]:
    db = database or settings.neo4j_database
    with get_driver().session(database=db) as s:
        yield s


def close() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def healthcheck() -> bool:
    try:
        with session() as s:
            s.run("RETURN 1 AS one").single()
        return True
    except Exception:
        return False
