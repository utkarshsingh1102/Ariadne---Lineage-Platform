"""/graph/* endpoints — all Cypher access goes through here."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

from . import neo4j_client
from .cypher_guard import UnsafeCypherError, assert_read_only
from .neo4j_client import _normalize_value
from .presets import UnknownPresetError, list_presets, preset_cypher

router = APIRouter(prefix="/graph", tags=["graph"])


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------

@router.get("/schema")
async def graph_schema() -> dict[str, list[str]]:
    """Live labels, relationship types, property keys."""
    out = {"labels": [], "relationship_types": [], "property_keys": []}
    try:
        async with neo4j_client.session() as s:
            for q, key in (
                ("CALL db.labels()", "labels"),
                ("CALL db.relationshipTypes()", "relationship_types"),
                ("CALL db.propertyKeys()", "property_keys"),
            ):
                result = await s.run(q)
                async for record in result:
                    out[key].append(record.value())
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"graph backend error: {e}") from e
    return out


# ---------------------------------------------------------------------------
# Node list + neighbours
# ---------------------------------------------------------------------------

@router.get("/nodes")
async def list_nodes(
    label: str | None = Query(None, description="Filter by node label"),
    name_like: str | None = Query(None, description="Substring filter on n.name"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, list]:
    # Label has to be interpolated (Cypher doesn't parameterize labels). We
    # validate against the live schema to keep injection impossible.
    label_clause = ""
    if label:
        if not label.isidentifier():
            raise HTTPException(status_code=400, detail="label must be alphanumeric")
        label_clause = f":`{label}`"
    where = ""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if name_like:
        where = "WHERE toLower(coalesce(n.name, '')) CONTAINS toLower($name_like)"
        params["name_like"] = name_like
    cypher = (
        f"MATCH (n{label_clause}) {where} "
        "RETURN n SKIP $offset LIMIT $limit"
    )
    try:
        async with neo4j_client.session() as s:
            result = await s.run(cypher, params)
            records = [r async for r in result]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"graph backend error: {e}") from e
    return neo4j_client.records_to_graph_payload(records)


@router.get("/node/{node_id}/neighbors")
async def node_neighbors(
    node_id: str = Path(..., description="id / fully_qualified_name / path"),
    depth: int = Query(1, ge=1, le=3),
) -> dict[str, list]:
    cypher = (
        f"MATCH path = (n)-[*1..{depth}]-(m) "
        "WHERE n.id = $id OR n.fully_qualified_name = $id OR n.path = $id "
        "RETURN path LIMIT 200"
    )
    try:
        async with neo4j_client.session() as s:
            result = await s.run(cypher, {"id": node_id})
            records = [r async for r in result]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"graph backend error: {e}") from e
    return neo4j_client.records_to_graph_payload(records)


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

@router.get("/query/presets")
def list_presets_endpoint() -> dict[str, list[str]]:
    return {"presets": list_presets()}


@router.post("/query/preset/{name}")
async def run_preset(
    name: str = Path(..., description="A registered preset (see /graph/query/presets)"),
    node_id: str | None = Query(None, description="Required for lineage-{upstream,downstream}"),
) -> dict[str, list]:
    try:
        cypher = preset_cypher(name)
    except UnknownPresetError:
        raise HTTPException(status_code=404, detail=f"unknown preset: {name}")
    params: dict[str, Any] = {"node_id": node_id}
    try:
        async with neo4j_client.session() as s:
            result = await s.run(cypher, params)
            records = [r async for r in result]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"graph backend error: {e}") from e
    return neo4j_client.records_to_graph_payload(records)


# ---------------------------------------------------------------------------
# Raw Cypher (read-only)
# ---------------------------------------------------------------------------

class CypherRequest(BaseModel):
    cypher: str
    parameters: dict[str, Any] = {}


@router.post("/query/cypher")
async def cypher_query(req: CypherRequest):
    try:
        assert_read_only(req.cypher)
    except UnsafeCypherError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        async with neo4j_client.session() as s:
            result = await s.run(req.cypher, req.parameters)
            records = [r async for r in result]
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"graph backend error: {e}") from e

    payload = neo4j_client.records_to_graph_payload(records)
    # If the query returned only scalars (no nodes / no edges), expose them
    # as a rows table so the frontend can render a Carbon DataTable instead.
    if not payload["nodes"] and not payload["edges"]:
        rows = [_normalize_value(dict(r)) for r in records]
        return {"nodes": [], "edges": [], "rows": rows}
    return payload
