"""Cypher queries + helper functions for reasoning about Connection ↔
DataFrame flow.

Two helpers are exposed:

* ``downstream_dataframes(connection_id)`` — DataFrames a *read* connection
  ultimately feeds (transitively, through any number of transforms).
* ``upstream_dataframes(connection_id)``   — DataFrames that ultimately
  feed a *write* connection.

The queries follow the graph the writer emits in ``graph/writer.py``:

    (Connection)-[:PROVIDES_DATAFRAME]->(DataFrame)
    (DataFrame)-[:DERIVES_FROM*0..]->(DataFrame)
    (DataFrame)-[:WRITES_TO_CONNECTION]->(Connection)

The wrapper functions accept either a live neo4j session or any object
exposing ``.run(cypher, **params)`` — keeping it test-friendly.
"""
from __future__ import annotations

from typing import Iterable, Protocol


class _Session(Protocol):
    def run(self, cypher: str, /, **params: object): ...


# Cypher templates exposed for callers that want to run them directly.

DOWNSTREAM_CYPHER = """
MATCH (c:Connection {id: $cid})-[:PROVIDES_DATAFRAME]->(seed:DataFrame)
OPTIONAL MATCH (seed)<-[:DERIVES_FROM*0..]-(df:DataFrame)
RETURN DISTINCT coalesce(df, seed) AS df
"""

UPSTREAM_CYPHER = """
MATCH (sink:DataFrame)-[:WRITES_TO_CONNECTION]->(c:Connection {id: $cid})
OPTIONAL MATCH (df:DataFrame)-[:DERIVES_FROM*0..]->(sink)
RETURN DISTINCT coalesce(df, sink) AS df
"""

BIDIRECTIONAL_CONNECTIONS_CYPHER = """
MATCH (c:Connection)
WHERE EXISTS { MATCH (c)-[:PROVIDES_DATAFRAME]->(:DataFrame) }
  AND EXISTS { MATCH (:DataFrame)-[:WRITES_TO_CONNECTION]->(c) }
RETURN c
"""

UNRESOLVED_CONNECTIONS_CYPHER = """
MATCH (c:Connection {resolved: false})
RETURN c
"""


def downstream_dataframes(session: _Session, connection_id: str) -> list[str]:
    """Return DataFrame IDs the given connection feeds (transitively).

    Returns an empty list if the connection has no read edges or the id
    isn't found — never throws.
    """
    result = session.run(DOWNSTREAM_CYPHER, cid=connection_id)
    out: list[str] = []
    for rec in result:
        df = rec.get("df")
        if df is None:
            continue
        df_id = df.get("id") if hasattr(df, "get") else getattr(df, "id", None)
        if df_id:
            out.append(df_id)
    return out


def upstream_dataframes(session: _Session, connection_id: str) -> list[str]:
    """Return DataFrame IDs that ultimately feed the given write connection."""
    result = session.run(UPSTREAM_CYPHER, cid=connection_id)
    out: list[str] = []
    for rec in result:
        df = rec.get("df")
        if df is None:
            continue
        df_id = df.get("id") if hasattr(df, "get") else getattr(df, "id", None)
        if df_id:
            out.append(df_id)
    return out


def bidirectional_connections(session: _Session) -> list[str]:
    """List connection IDs that act as both a source AND a sink — the
    canonical "same DB read+write" pattern.
    """
    result = session.run(BIDIRECTIONAL_CONNECTIONS_CYPHER)
    return [rec["c"]["id"] for rec in result if "id" in rec["c"]]


def unresolved_connections(session: _Session) -> list[str]:
    """List Connection IDs whose target was env/secret/dynamic at parse time."""
    result = session.run(UNRESOLVED_CONNECTIONS_CYPHER)
    return [rec["c"]["id"] for rec in result if "id" in rec["c"]]


# ---------------------------------------------------------------------------
# Pure-IR helpers — same shape, in-memory only. Useful in tests where we
# don't want a live Neo4j session.
# ---------------------------------------------------------------------------


def _ir_collect_dataframes_for_connection(
    dataframes: Iterable, connection_id: str, *, side: str,
) -> list[str]:
    """``side='read'``: DataFrames whose reads_from connections include ``cid``.
    ``side='write'``: DataFrames whose write_edges target connection is ``cid``.

    Returns DataFrame ids in source order.
    """
    matches: list[str] = []
    for df in dataframes:
        ok = False
        if side == "read":
            for tbl in getattr(df, "reads_from", []):
                conn = getattr(tbl, "connection", None)
                if conn and getattr(conn, "id", None) == connection_id:
                    ok = True
                    break
        elif side == "write":
            for edge in getattr(df, "write_edges", []):
                conn = getattr(edge.target, "connection", None)
                if conn and getattr(conn, "id", None) == connection_id:
                    ok = True
                    break
        if ok and df.id:
            matches.append(df.id)
    return matches


def ir_downstream_dataframes(ir, connection_id: str) -> list[str]:
    """Pure-IR version of ``downstream_dataframes`` for unit tests."""
    return _ir_collect_dataframes_for_connection(
        ir.dataframes, connection_id, side="read",
    )


def ir_upstream_dataframes(ir, connection_id: str) -> list[str]:
    """Pure-IR version of ``upstream_dataframes`` for unit tests."""
    return _ir_collect_dataframes_for_connection(
        ir.dataframes, connection_id, side="write",
    )
