"""QlikView parser — top-level package.

Exposes:
* ``GraphDatabase`` — re-exported from ``neo4j``; tests patch ``qlikview_parser.GraphDatabase``
  to inject a mock driver.
* ``QlikViewParser`` — the main entry point.
* IR data classes — ``QlikViewApp``, ``LoadStatement``, ``Connection``, ``Field``, ``Join``,
  ``SourceType``, ``ConnectionType``.
"""
from __future__ import annotations

from neo4j import GraphDatabase

from .core import QlikViewParser
from .models import (
    Concatenation,
    Connection,
    ConnectionType,
    Field,
    Join,
    LoadStatement,
    QlikViewApp,
    SourceType,
    Subroutine,
    Variable,
)

__all__ = [
    "GraphDatabase",
    "QlikViewParser",
    "QlikViewApp",
    "LoadStatement",
    "Connection",
    "ConnectionType",
    "Field",
    "Join",
    "SourceType",
    "Variable",
    "Subroutine",
    "Concatenation",
]
