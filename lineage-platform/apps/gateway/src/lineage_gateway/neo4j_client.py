"""Async Neo4j driver wrapper + GraphPayload converter."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from neo4j import AsyncGraphDatabase, AsyncSession
from neo4j.time import Date as Neo4jDate, DateTime as Neo4jDateTime, Duration as Neo4jDuration, Time as Neo4jTime

from .config import Settings


def _normalize_value(v: Any) -> Any:
    # Neo4j temporal types aren't JSON-serializable by Pydantic. The QlikView
    # writer stamps every node with datetime() ingested_at/last_seen_at, so
    # any unguarded payload trips PydanticSerializationError and the route
    # returns 500 — the frontend then renders "0 nodes · 0 edges".
    if isinstance(v, (Neo4jDateTime, Neo4jDate, Neo4jTime)):
        return v.iso_format()
    if isinstance(v, Neo4jDuration):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [_normalize_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _normalize_value(x) for k, x in v.items()}
    return v


def _normalize_props(props: dict[str, Any]) -> dict[str, Any]:
    return {k: _normalize_value(v) for k, v in props.items()}


_driver = None


async def init_driver(settings: Settings) -> None:
    global _driver
    _driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )


async def close_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


def get_driver():
    if _driver is None:
        raise RuntimeError("Neo4j driver not initialised — call init_driver() first")
    return _driver


@asynccontextmanager
async def session(database: str | None = None) -> AsyncIterator[AsyncSession]:
    drv = get_driver()
    s = drv.session(database=database) if database else drv.session()
    try:
        yield s
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# Record → cytoscape-shaped GraphPayload
# ---------------------------------------------------------------------------

_SOURCE_SYSTEM_LABEL_PREFIX = {
    # Map any label we know about to the parser that owns it.
    "Tableau": "tableau",
    "Qlik": "qlikview",
    "TWS": "tws",
    "Spark": "spark",
}


def _source_system(labels: list[str], properties: dict[str, Any]) -> str:
    if properties.get("source_system"):
        return str(properties["source_system"])
    for label in labels:
        for prefix, system in _SOURCE_SYSTEM_LABEL_PREFIX.items():
            if label.startswith(prefix):
                return system
    if "Table" in labels or "Attribute" in labels:
        return "shared"
    if "Script" in labels:
        return "shared"
    if "Connection" in labels:
        return "shared"
    return "unknown"


def _primary_label(labels: list[str]) -> str:
    # First non-shared label wins; fall back to the first label.
    for label in labels:
        if label not in {"Table", "Attribute", "Script", "Connection"}:
            return label
    return labels[0] if labels else "Node"


def node_to_dict(node) -> dict[str, Any]:
    labels = list(node.labels)
    props = dict(node.items())
    nid = props.get("id") or props.get("fully_qualified_name") or props.get("path") or str(node.element_id)
    return {
        "data": {
            "id": str(nid),
            "label": _primary_label(labels),
            "labels": labels,
            "source_system": _source_system(labels, props),
            "properties": _normalize_props(props),
        }
    }


def rel_to_dict(rel) -> dict[str, Any]:
    # The driver's element_id is unique within the running database.
    start_node = rel.start_node
    end_node = rel.end_node
    s_props = dict(start_node.items()) if start_node is not None else {}
    e_props = dict(end_node.items()) if end_node is not None else {}
    sid = s_props.get("id") or s_props.get("fully_qualified_name") or s_props.get("path") or str(start_node.element_id)
    tid = e_props.get("id") or e_props.get("fully_qualified_name") or e_props.get("path") or str(end_node.element_id)
    return {
        "data": {
            "id": str(rel.element_id),
            "source": str(sid),
            "target": str(tid),
            "label": rel.type,
            "properties": _normalize_props(dict(rel.items())),
        }
    }


def records_to_graph_payload(records: list) -> dict[str, list]:
    """Walk an iterable of neo4j Records and build a deduplicated payload."""
    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    for record in records:
        for value in record.values():
            _walk(value, nodes, edges)
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def _walk(value, nodes: dict[str, dict], edges: dict[str, dict]) -> None:
    # Path-like
    if hasattr(value, "nodes") and hasattr(value, "relationships"):
        for n in value.nodes:
            d = node_to_dict(n)
            nodes[d["data"]["id"]] = d
        for r in value.relationships:
            d = rel_to_dict(r)
            edges[d["data"]["id"]] = d
        return
    # Node
    if hasattr(value, "labels"):
        d = node_to_dict(value)
        nodes[d["data"]["id"]] = d
        return
    # Relationship
    if hasattr(value, "type") and hasattr(value, "start_node"):
        d = rel_to_dict(value)
        edges[d["data"]["id"]] = d
        return
    # List/tuple — recurse
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk(item, nodes, edges)
        return
    # Plain scalar — skip (records_to_table is the right place for these)
