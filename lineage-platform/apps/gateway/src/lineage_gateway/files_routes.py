"""/files — catalogue of parsed source artefacts.

We never store file contents — only the metadata that already lives in Neo4j
(name, file_path, id, source-specific fields). Each row is the top-level
"file" node for its parser:

- Tableau    → :TableauWorkbook  (one per .twb / .twbx)
- QlikView   → :QlikScript       (one per .qvs)
- Spark      → :SparkScript      (one per .py / .sql / notebook)
- TWS        → :Schedule         (TWS files yield multiple schedules — we
                                  surface schedules as the addressable unit)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import neo4j_client

router = APIRouter(prefix="/files", tags=["files"])

# Hard cap on bytes returned by the source-code endpoint. The viewer is for
# inspection, not bulk export — virtualisation on the frontend kicks in well
# before this ceiling. Anything larger truncates with ``truncated: true``.
_MAX_SOURCE_BYTES = 2 * 1024 * 1024  # 2 MiB

# The parsers run in Docker and persist canonical container paths into Neo4j
# (``/data/uploads/...``, ``/data/inputs/...``). The gateway runs as a host
# uvicorn process via ``start.sh`` and never sees those paths. Resolve a
# persisted path to its host equivalent by walking the rewrite list below.
#
# Anchored from ``files_routes.py``:
#   .../lineage-platform/apps/gateway/src/lineage_gateway/files_routes.py
# parents[0]=lineage_gateway, [1]=src, [2]=gateway, [3]=apps,
# parents[4]=lineage-platform, parents[5]=repo root.
_PLATFORM_DIR = Path(__file__).resolve().parents[4]   # lineage-platform/
_REPO_ROOT = _PLATFORM_DIR.parent                     # repo root

_PATH_REWRITES: list[tuple[str, Path]] = [
    ("/data/uploads/", _PLATFORM_DIR / "uploads"),
    ("/data/inputs/", _REPO_ROOT / "spark-parser" / "fixtures"),
]


async def _resolve_tws_label(source: str, primary: str, file_id: str) -> str:
    """For TWS, return :TwsFile if a node with that id exists; else fall
    back to :Schedule (legacy parses that predate the file wrapper). For
    non-TWS sources, return the primary label unchanged.
    """
    if source != "tws":
        return primary
    cypher = "MATCH (n) WHERE n.id = $id RETURN labels(n)[0] AS lbl LIMIT 1"
    try:
        async with neo4j_client.session() as s:
            row = await (await s.run(cypher, id=file_id)).single()
    except Exception:
        return primary
    if row is None:
        return primary
    lbl = row["lbl"]
    return lbl if lbl in {"TwsFile", "Schedule"} else primary


def _resolve_host_path(persisted: str) -> Path:
    """Map a parser-container path back to the host filesystem.

    Absolute host paths (e.g. ``/Users/...``) are returned unchanged so the
    endpoint also works for files parsed via a direct host path. Anything
    that doesn't match a rewrite prefix is returned as-is too — the caller
    will surface a 410 with the unresolved path if it doesn't exist.
    """
    for prefix, host_root in _PATH_REWRITES:
        if persisted.startswith(prefix):
            relative = persisted[len(prefix):]
            return host_root / relative
    return Path(persisted)


# Each query returns a uniform shape: id, name, file_path (or path), plus a
# couple of optional fields the frontend uses for context. Missing properties
# come back as null — that's fine, the frontend tolerates it.
_QUERIES: dict[str, str] = {
    "tableau": (
        "MATCH (n:TableauWorkbook) "
        "RETURN n.id AS id, "
        "       coalesce(n.name, '<unnamed>') AS name, "
        "       n.file_path AS file_path, "
        "       n.version AS version, "
        "       n.parsed_at AS parsed_at, "
        "       'TableauWorkbook' AS type "
        "ORDER BY toLower(coalesce(n.name, n.id)) "
        "LIMIT 1000"
    ),
    "qlikview": (
        "MATCH (n:QlikScript) "
        "RETURN n.id AS id, "
        "       coalesce(n.name, n.app_name, '<unnamed>') AS name, "
        "       n.file_path AS file_path, "
        "       n.parsed_at AS parsed_at, "
        "       'QlikScript' AS type "
        "ORDER BY toLower(coalesce(n.name, n.app_name, n.id)) "
        "LIMIT 1000"
    ),
    "spark": (
        "MATCH (n:SparkScript) "
        "RETURN n.id AS id, "
        "       coalesce(n.name, '<unnamed>') AS name, "
        "       n.file_path AS file_path, "
        "       n.script_type AS script_type, "
        "       n.parsed_at AS parsed_at, "
        "       'SparkScript' AS type "
        "ORDER BY toLower(coalesce(n.name, n.id)) "
        "LIMIT 1000"
    ),
    # v0.3 — :TwsFile is the file-level wrapper around N :Schedule nodes
    # produced from one upload. We also union-in any :Schedule nodes that
    # don't have a parent :TwsFile (legacy data parsed before the file
    # node existed) so the Files page stays populated for old graphs.
    "tws": (
        "MATCH (f:TwsFile) "
        "RETURN f.id AS id, "
        "       f.name AS name, "
        "       f.file_path AS file_path, "
        "       f.schedule_count AS schedule_count, "
        "       f.parsed_at AS parsed_at, "
        "       'TwsFile' AS type "
        "UNION "
        "MATCH (s:Schedule) "
        "WHERE NOT (s)<-[:CONTAINS_SCHEDULE]-(:TwsFile) "
        "RETURN s.id AS id, "
        "       coalesce(s.name, '<unnamed>') AS name, "
        "       NULL AS file_path, "
        "       1 AS schedule_count, "
        "       s.parsed_at AS parsed_at, "
        "       'Schedule' AS type "
        "ORDER BY toLower(coalesce(name, id)) "
        "LIMIT 1000"
    ),
}


@router.get("")
async def list_files() -> dict[str, list[dict[str, Any]]]:
    """Return parsed files grouped by source system.

    Empty list per source when nothing has been ingested yet. Per-source
    failures are isolated — one broken query won't blank the rest.
    """
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in _QUERIES}
    for source, cypher in _QUERIES.items():
        try:
            async with neo4j_client.session() as s:
                result = await s.run(cypher)
                rows: list[dict[str, Any]] = []
                async for record in result:
                    rows.append({k: record[k] for k in record.keys()})
                out[source] = rows
        except Exception as e:
            # Surface a soft error per-source rather than 500-ing the whole call.
            out[source] = []
            out[f"_error_{source}"] = [{"detail": str(e)}]  # type: ignore[assignment]
    return out


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

# Containment relationships per source. Walking these from the file node
# yields every node the parser owns exclusively. Shared nodes (:Table,
# :Connection, :Attribute that's HAS_COLUMN'd from a Table) are filtered
# at the DELETE step so a Tableau workbook deletion doesn't take out tables
# that QlikView or Spark still reference.
_DELETE_LABEL: dict[str, str] = {
    "tableau": "TableauWorkbook",
    "qlikview": "QlikScript",
    "spark": "SparkScript",
    # v0.3 — :TwsFile is the new file-level wrapper. Legacy parses that
    # only produced :Schedule still appear in the listing via the UNION
    # above; the delete here matches the same id-shape (file-or-schedule).
    "tws": "TwsFile",
}

# Per-source containment chain. Variable-length expansion picks up nested
# descendants (e.g. Workbook → Datasource → Parameter).
_DELETE_CONTAINMENT: dict[str, str] = {
    "tableau": (
        "CONTAINS_DATASOURCE|CONTAINS_DASHBOARD|CONTAINS_WORKSHEET"
        "|HAS_PARAMETER|DISPLAYS_WORKSHEET"
    ),
    "qlikview": (
        "CONTAINS_TABLE|CONTAINS_CHART|CONTAINS_SHEET"
        "|HAS_VARIABLE|HAS_SUBROUTINE|USES_FIELD"
        # v0.2 / Phase 3 — attribute-level containment + Sense /
        # server-meta surfaces so a delete sweeps the full subgraph.
        "|HAS_ATTRIBUTE|HAS_CONSTRAINT|STORED_AS|FEEDS_OBJECT"
    ),
    "spark": "CONTAINS_DATAFRAME|HAS_FIELD",
    # TwsFile → Schedule → Jobs/Components → Scripts/Resources.
    "tws": (
        "CONTAINS_SCHEDULE|CONTAINS_JOB|CONTAINS_COMPONENT"
        "|CALLS_SCRIPT|HAS_RESOURCE|EXECUTES|REQUIRES_RESOURCE"
        "|WAITS_FOR_PROMPT|WAITS_FOR_FILE|HOSTS_STREAM|SCHEDULED_BY"
    ),
}


@router.delete("/{source}/{file_id}")
async def delete_file(source: str, file_id: str) -> dict[str, Any]:
    """Remove a parsed file from Neo4j along with the nodes it exclusively owns.

    Shared label nodes (:Table, :Connection) are left intact even if this
    was the only file referencing them — they may carry value beyond a
    single parse and we don't want a delete here to silently take them
    out from under another parser.
    """
    label = _DELETE_LABEL.get(source)
    if label is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown source: {source!r} (expected one of {sorted(_DELETE_LABEL)})",
        )
    # For TWS, a row in the file list may carry either label depending on
    # whether the parse produced a :TwsFile wrapper. Probe both.
    label = await _resolve_tws_label(source, label, file_id)
    rel_chain = _DELETE_CONTAINMENT[source]
    # Step 1: confirm the file exists (so we can return 404 cleanly).
    # Step 2: collect + delete its exclusive subgraph.
    exists_cypher = f"MATCH (f:`{label}` {{id: $file_id}}) RETURN f LIMIT 1"
    delete_cypher = (
        f"MATCH (f:`{label}` {{id: $file_id}}) "
        f"OPTIONAL MATCH (f)-[:{rel_chain}*1..6]->(child) "
        "WHERE child IS NOT NULL "
        "  AND NOT child:Table "
        "  AND NOT child:Connection "
        "  AND child <> f "
        "WITH f, collect(DISTINCT child) AS children "
        "WITH f, children, size(children) AS n_children "
        "FOREACH (n IN children | DETACH DELETE n) "
        "DETACH DELETE f "
        "RETURN n_children + 1 AS nodes_deleted"
    )
    try:
        async with neo4j_client.session() as s:
            exists = await (await s.run(exists_cypher, file_id=file_id)).single()
            if exists is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"no :{label} found with id={file_id!r}",
                )
            record = await (await s.run(delete_cypher, file_id=file_id)).single()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"neo4j delete failed: {e}") from e
    deleted = int(record["nodes_deleted"]) if record else 0
    return {
        "deleted": True,
        "source": source,
        "file_id": file_id,
        "nodes_deleted": deleted,
    }


# ---------------------------------------------------------------------------
# Bulk delete — apply the same per-file delete subgraph walk to N files in one
# request. Each file is processed independently inside its own try block so
# that one bad id (e.g. already deleted, unknown source) doesn't take the rest
# of the batch down. The response is a per-file roll-up plus aggregate counts.
# ---------------------------------------------------------------------------

class _BulkDeleteItem(BaseModel):
    source: str = Field(..., description="tableau | tws | qlikview | spark")
    file_id: str = Field(..., description="The node id of the file to delete")


class BulkDeleteRequest(BaseModel):
    files: list[_BulkDeleteItem] = Field(
        ..., min_length=1, max_length=500,
        description="1-500 files to delete. Larger batches should page client-side.",
    )


@router.post("/bulk-delete")
async def bulk_delete_files(req: BulkDeleteRequest) -> dict[str, Any]:
    """Delete N parsed files in one round-trip.

    Each file is processed independently using the same per-source subgraph
    walk as the single-file DELETE — shared :Table / :Connection nodes stay
    intact. A failure on one file (unknown source, 404, Neo4j error) is
    recorded in ``results`` for that file but doesn't abort the batch.
    """
    results: list[dict[str, Any]] = []
    succeeded = 0
    nodes_total = 0

    async with neo4j_client.session() as s:
        for item in req.files:
            label = _DELETE_LABEL.get(item.source)
            if label is not None:
                label = await _resolve_tws_label(item.source, label, item.file_id)
            if label is None:
                results.append({
                    "source": item.source,
                    "file_id": item.file_id,
                    "deleted": False,
                    "nodes_deleted": 0,
                    "error": f"unknown source: {item.source!r}",
                })
                continue
            rel_chain = _DELETE_CONTAINMENT[item.source]
            exists_cypher = f"MATCH (f:`{label}` {{id: $file_id}}) RETURN f LIMIT 1"
            delete_cypher = (
                f"MATCH (f:`{label}` {{id: $file_id}}) "
                f"OPTIONAL MATCH (f)-[:{rel_chain}*1..6]->(child) "
                "WHERE child IS NOT NULL "
                "  AND NOT child:Table "
                "  AND NOT child:Connection "
                "  AND child <> f "
                "WITH f, collect(DISTINCT child) AS children "
                "WITH f, children, size(children) AS n_children "
                "FOREACH (n IN children | DETACH DELETE n) "
                "DETACH DELETE f "
                "RETURN n_children + 1 AS nodes_deleted"
            )
            try:
                exists = await (await s.run(exists_cypher, file_id=item.file_id)).single()
                if exists is None:
                    results.append({
                        "source": item.source,
                        "file_id": item.file_id,
                        "deleted": False,
                        "nodes_deleted": 0,
                        "error": f"no :{label} with id={item.file_id!r}",
                    })
                    continue
                record = await (await s.run(delete_cypher, file_id=item.file_id)).single()
                deleted = int(record["nodes_deleted"]) if record else 0
                nodes_total += deleted
                succeeded += 1
                results.append({
                    "source": item.source,
                    "file_id": item.file_id,
                    "deleted": True,
                    "nodes_deleted": deleted,
                })
            except Exception as e:
                results.append({
                    "source": item.source,
                    "file_id": item.file_id,
                    "deleted": False,
                    "nodes_deleted": 0,
                    "error": f"neo4j delete failed: {e}",
                })

    return {
        "requested": len(req.files),
        "succeeded": succeeded,
        "failed": len(req.files) - succeeded,
        "nodes_deleted": nodes_total,
        "results": results,
    }


# Per-source label that the source-code endpoint dispatches on. The id field
# we MATCH against is uniformly ``id``; the property holding the on-disk path
# is uniformly ``file_path`` across every parser today.
_SOURCE_LABEL: dict[str, str] = {
    "tableau": "TableauWorkbook",
    "qlikview": "QlikScript",
    "spark": "SparkScript",
    "tws": "TwsFile",
}


@router.get("/{source}/{file_id}/source")
async def file_source(source: str, file_id: str) -> dict[str, Any]:
    """Return the raw on-disk text of a parsed file plus light metadata.

    Used by the lineage tracer's "View Source" panel to show the script
    behind a trace. Never executes or transforms the file — straight read,
    UTF-8 decode, hard-capped at ``_MAX_SOURCE_BYTES``.
    """
    label = _SOURCE_LABEL.get(source)
    if label is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown source: {source!r} "
                f"(expected one of {sorted(_SOURCE_LABEL)})"
            ),
        )

    # Look up the on-disk path Neo4j knows about. Trust the property only —
    # don't accept a path from the caller; otherwise the endpoint becomes
    # an arbitrary-file-read primitive.
    cypher = (
        f"MATCH (n:`{label}` {{id: $file_id}}) "
        "RETURN n.file_path AS file_path, n.name AS name"
    )
    try:
        async with neo4j_client.session() as s:
            record = await (await s.run(cypher, file_id=file_id)).single()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"neo4j lookup failed: {e}") from e
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"no :{label} found with id={file_id!r}",
        )

    file_path = record["file_path"]
    if not file_path:
        raise HTTPException(
            status_code=409,
            detail=f"{label} {file_id!r} has no file_path property",
        )

    p = _resolve_host_path(file_path)
    if not p.exists() or not p.is_file():
        raise HTTPException(
            status_code=410,
            detail=(
                f"file at {file_path!r} (resolved {str(p)!r}) is no longer "
                f"available on the gateway host — reparse to refresh"
            ),
        )

    try:
        size = p.stat().st_size
        truncated = size > _MAX_SOURCE_BYTES
        with p.open("rb") as fh:
            raw = fh.read(_MAX_SOURCE_BYTES if truncated else size)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"read failed: {e}") from e

    # The parser writes Python — UTF-8 is the safe default. Replace on bad
    # bytes so a stray non-UTF char never blanks the panel.
    text = raw.decode("utf-8", errors="replace")
    return {
        "file_id": file_id,
        "source": source,
        "file_path": file_path,
        "name": record["name"],
        "language": _language_for(file_path),
        "size_bytes": size,
        "truncated": truncated,
        "line_count": text.count("\n") + (0 if text.endswith("\n") else 1),
        "source_code": text,
    }


def _language_for(file_path: str) -> str:
    """Crude extension-based language tag the frontend uses to pick a
    Prism grammar. Defaults to plain text when unrecognised."""
    ext = os.path.splitext(file_path)[1].lower()
    return {
        ".py": "python",
        ".sql": "sql",
        ".ipynb": "json",
        ".qvs": "qlik",
        ".twb": "xml",
        ".twbx": "xml",
    }.get(ext, "plaintext")


@router.get("/summary")
async def files_summary() -> dict[str, int]:
    """Lightweight counts per source — fast enough for the Dashboard."""
    counts: dict[str, int] = {}
    for source, cypher in _QUERIES.items():
        try:
            count_cypher = cypher.split(" RETURN ")[0] + " RETURN count(n) AS c"
            async with neo4j_client.session() as s:
                result = await s.run(count_cypher)
                record = await result.single()
                counts[source] = int(record["c"]) if record else 0
        except Exception:
            counts[source] = 0
    return counts
