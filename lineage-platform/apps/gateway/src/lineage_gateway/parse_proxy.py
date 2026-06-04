"""/parse — dispatches to per-parser FastAPI services via HTTPX."""
from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .config import get_settings

router = APIRouter(prefix="/parse", tags=["parse"])

# Host-side uploads dir. The same path is mounted into every parser at /data/uploads.
# We resolve relative to this file: apps/gateway/src/lineage_gateway/parse_proxy.py
# → ../../../../uploads (lineage-platform/uploads). Override with UPLOAD_DIR env var.
_DEFAULT_UPLOAD_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent / "uploads"
)
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(_DEFAULT_UPLOAD_DIR)))
# Path the same file appears at inside every parser container.
CONTAINER_UPLOAD_DIR = "/data/uploads"
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))

# Whitelist accepted file suffixes per source_type to avoid blindly accepting binaries.
_ACCEPTED_SUFFIXES: dict[str, set[str]] = {
    "tableau": {".twb", ".twbx"},
    "tws": {".txt", ".xml"},
    "qlikview": {".qvs", ".qvw", ".qvf"},
    "spark": {".py", ".sql", ".ipynb", ".dbc"},
}

# Reverse map used by the smart-batch /upload/auto endpoint: suffix → source_type.
# Built once from _ACCEPTED_SUFFIXES so the two stay in lockstep — adding a new
# suffix to any parser's whitelist also makes auto-routing recognise it.
# NOTE: .xml currently routes to TWS. If another parser ever accepts .xml the
# map will need a more sophisticated classifier (e.g. content sniff via the
# format detector), not just suffix lookup.
_SUFFIX_TO_SOURCE: dict[str, str] = {
    suffix: source
    for source, suffixes in _ACCEPTED_SUFFIXES.items()
    for suffix in suffixes
}


def detect_source_type(filename: str) -> str | None:
    """Classify a filename to its parser by suffix.

    Returns the source_type (``tableau``/``tws``/``qlikview``/``spark``) or
    None if the suffix isn't recognised by any parser.
    """
    suffix = Path(filename).suffix.lower()
    return _SUFFIX_TO_SOURCE.get(suffix)


class ParseRequest(BaseModel):
    source_type: str = Field(..., description="tableau | tws | qlikview | spark")
    file_path: str = Field(..., description="Absolute path inside the parser's mounted volume")
    overwrite: bool = False


def _target_url(source_type: str) -> str:
    s = get_settings()
    mapping = {
        "tableau": s.parser_tableau_url,
        "tws": s.parser_tws_url,
        "qlikview": s.parser_qlikview_url,
        "spark": s.parser_spark_url,
    }
    if source_type not in mapping:
        raise HTTPException(status_code=400, detail=f"unknown source_type: {source_type}")
    return mapping[source_type]


@router.post("")
async def parse(req: ParseRequest) -> dict[str, Any]:
    base = _target_url(req.source_type)
    payload = {"file_path": req.file_path, "overwrite": req.overwrite}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{base}/parse", json=payload)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"{req.source_type}-parser unreachable: {e}",
        ) from e
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=resp.status_code,
            detail=resp.text,
        )
    body = resp.json()
    body.setdefault("source_type", req.source_type)
    body["id"] = (
        body.get("id")
        or body.get("workbook_id")
        or body.get("script_id")
        or body.get("schedule_id")
    )
    return body


@router.post("/upload")
async def parse_upload(
    source_type: str = Form(..., description="tableau | tws | qlikview | spark"),
    file: UploadFile = File(...),
    overwrite: bool = Form(False),
) -> dict[str, Any]:
    """Accept a multipart file, save it to the shared uploads volume,
    then dispatch the parser using the in-container path."""
    if source_type not in _ACCEPTED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"unknown source_type: {source_type}")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ACCEPTED_SUFFIXES[source_type]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{source_type} expects one of "
                f"{sorted(_ACCEPTED_SUFFIXES[source_type])}; got {suffix or '<none>'}"
            ),
        )

    # Save under uploads/<uuid>/<original_filename> rather than
    # uploads/<uuid>_<original> — that way Path(file_path).stem on the
    # parser side yields the clean original name (no uuid noise in the
    # File Explorer or graph). The uuid subdir keeps uploads collision-free.
    safe_name = Path(file.filename or "upload").name  # strip path components
    upload_key = uuid.uuid4().hex[:8]
    host_dir = UPLOAD_DIR / upload_key
    host_dir.mkdir(parents=True, exist_ok=True)
    host_path = host_dir / safe_name
    saved_name = f"{upload_key}/{safe_name}"

    bytes_written = 0
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    try:
        with host_path.open("wb") as out:
            while chunk := await file.read(1 << 20):  # 1 MiB chunks
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    out.close()
                    host_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"file exceeds MAX_UPLOAD_MB={MAX_UPLOAD_MB}",
                    )
                out.write(chunk)
    finally:
        await file.close()

    container_path = f"{CONTAINER_UPLOAD_DIR}/{upload_key}/{safe_name}"
    base = _target_url(source_type)
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                f"{base}/parse",
                json={"file_path": container_path, "overwrite": overwrite},
            )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"{source_type}-parser unreachable: {e}",
        ) from e

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    body = resp.json()
    body.setdefault("source_type", source_type)
    # Normalise the parser's specific id field into a single `id` so the
    # frontend doesn't have to know per-parser shapes.
    body["id"] = (
        body.get("id")
        or body.get("workbook_id")
        or body.get("script_id")
        or body.get("schedule_id")
    )
    body["uploaded_as"] = saved_name
    body["original_filename"] = safe_name
    return body


@router.post("/upload/multi")
async def parse_upload_multi(
    source_type: str = Form(..., description="tws (others not yet supported)"),
    files: list[UploadFile] = File(..., description="2-20 files to analyse together"),
    overwrite: bool = Form(False),
) -> dict[str, Any]:
    """Accept N multipart files, save them under a shared batch directory,
    then dispatch the parser's ``POST /parse/multi`` with the in-container
    paths. The parser merges the N units and returns shared entities +
    cross-file FOLLOWS analysis.

    Currently TWS-only — other parsers can opt in by implementing their
    own ``/parse/multi`` endpoint.
    """
    if source_type != "tws":
        raise HTTPException(
            status_code=400,
            detail=f"/upload/multi is currently tws-only; got {source_type!r}",
        )
    if len(files) < 2:
        raise HTTPException(
            status_code=400,
            detail="upload/multi requires at least 2 files",
        )
    if len(files) > 20:
        raise HTTPException(
            status_code=400,
            detail="upload/multi capped at 20 files per batch",
        )

    accepted = _ACCEPTED_SUFFIXES.get(source_type, set())
    batch_uuid = uuid.uuid4().hex[:8]
    host_dir = UPLOAD_DIR / batch_uuid
    host_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    container_paths: list[str] = []
    uploaded_as: list[str] = []
    saved_paths: list[Path] = []
    try:
        for f in files:
            safe_name = Path(f.filename or "upload").name
            suffix = Path(safe_name).suffix.lower()
            if suffix not in accepted:
                # Roll back partial saves on validation failure.
                _cleanup(saved_paths)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{source_type} expects one of {sorted(accepted)}; "
                        f"got {suffix or '<none>'} for {safe_name!r}"
                    ),
                )
            host_path = host_dir / safe_name
            bytes_written = 0
            with host_path.open("wb") as out:
                while chunk := await f.read(1 << 20):
                    bytes_written += len(chunk)
                    if bytes_written > max_bytes:
                        out.close()
                        host_path.unlink(missing_ok=True)
                        _cleanup(saved_paths)
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"file {safe_name!r} exceeds MAX_UPLOAD_MB="
                                f"{MAX_UPLOAD_MB}"
                            ),
                        )
                    out.write(chunk)
            await f.close()
            saved_paths.append(host_path)
            container_paths.append(f"{CONTAINER_UPLOAD_DIR}/{batch_uuid}/{safe_name}")
            uploaded_as.append(f"{batch_uuid}/{safe_name}")
    finally:
        for f in files:
            try:
                await f.close()
            except Exception:
                pass

    base = _target_url(source_type)
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{base}/parse/multi",
                json={
                    "file_paths": container_paths,
                    "overwrite": overwrite,
                },
            )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"{source_type}-parser unreachable: {e}",
        ) from e

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    body = resp.json()
    body["batch_uuid"] = batch_uuid
    body["uploaded_as"] = uploaded_as
    return body


def _cleanup(paths: list[Path]) -> None:
    """Best-effort rollback of partial multi-file uploads on validation failure."""
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


@router.post("/upload/auto")
async def parse_upload_auto(
    files: list[UploadFile] = File(..., description="1-30 mixed-source files"),
    overwrite: bool = Form(False),
    project_name: str = Form(
        ...,
        description=(
            "Required. Every successfully-parsed file in this batch is "
            "grouped into this project (created if it doesn't already "
            "exist; existing projects are appended to). The name must be "
            "unique across all projects when created."
        ),
    ),
) -> dict[str, Any]:
    """Heterogeneous batch upload — accept N files of any supported types,
    classify each by file suffix, and dispatch every file to its matching
    parser's ``/parse`` endpoint concurrently.

    Unlike ``/upload/multi`` (which merges N TWS files into a single
    cross-file lineage analysis), this endpoint treats each file as an
    INDEPENDENT parse and returns per-file results. The user can mix any
    combination of Tableau, TWS, QlikView, and Spark files in a single
    upload — the system identifies the type from the extension and routes
    each to the right parser without further prompting.

    Files with unrecognised suffixes are still saved + reported as
    ``unsupported`` so the user sees what was skipped (no silent drops).
    """
    if not files:
        raise HTTPException(status_code=400, detail="no files supplied")
    if len(files) > 30:
        raise HTTPException(
            status_code=400, detail="upload/auto capped at 30 files per batch"
        )
    if not project_name or not project_name.strip():
        raise HTTPException(
            status_code=400,
            detail=(
                "project_name is required for batch uploads — every batch "
                "must be grouped under a project so the Files tab can list "
                "the files together."
            ),
        )

    batch_uuid = uuid.uuid4().hex[:8]
    host_dir = UPLOAD_DIR / batch_uuid
    host_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024

    # Step 1 — save every file + classify each. Unsupported suffixes are
    # captured for the response but skipped from dispatch.
    items: list[dict[str, Any]] = []  # one entry per uploaded file
    try:
        for f in files:
            safe_name = Path(f.filename or "upload").name
            source_type = detect_source_type(safe_name)
            host_path = host_dir / safe_name
            bytes_written = 0
            with host_path.open("wb") as out:
                while chunk := await f.read(1 << 20):
                    bytes_written += len(chunk)
                    if bytes_written > max_bytes:
                        out.close()
                        host_path.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"file {safe_name!r} exceeds MAX_UPLOAD_MB="
                                f"{MAX_UPLOAD_MB}"
                            ),
                        )
                    out.write(chunk)
            await f.close()
            items.append({
                "original_filename": safe_name,
                "source_type": source_type,
                "host_path": host_path,
                "container_path": f"{CONTAINER_UPLOAD_DIR}/{batch_uuid}/{safe_name}",
                "uploaded_as": f"{batch_uuid}/{safe_name}",
            })
    finally:
        for f in files:
            try:
                await f.close()
            except Exception:
                pass

    # Step 2 — dispatch each classified file to its parser.
    #
    # IMPORTANT: when ≥2 of these files target the SAME parser AND that
    # parser exposes a /parse/multi merge endpoint (TWS today), route them
    # through /parse/multi instead of N independent /parse calls — only
    # then does the cross-file FOLLOWS edge resolution actually run and
    # the DEPENDS_ON edges between jobs in different files land in Neo4j.
    # Without this, "auto-batch with project" creates the project but the
    # explorer's combined-lineage view shows no connections between the
    # grouped TWS files (the v0.2 cross-file resolver was never invoked).
    #
    # Other parsers (Tableau, QlikView, Spark) keep their independent
    # per-file /parse dispatch.
    MULTI_CAPABLE_SOURCES = {"tws"}

    def _unsupported(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "original_filename": item["original_filename"],
            "uploaded_as": item["uploaded_as"],
            "source_type": None,
            "status": "unsupported",
            "detail": (
                f"Unrecognised file extension — no parser claims "
                f"{Path(item['original_filename']).suffix.lower() or '<none>'}"
            ),
        }

    async def _dispatch_single(item: dict[str, Any]) -> dict[str, Any]:
        base = _target_url(item["source_type"])
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{base}/parse",
                    json={
                        "file_path": item["container_path"],
                        "overwrite": overwrite,
                    },
                )
        except httpx.RequestError as e:
            return {
                "original_filename": item["original_filename"],
                "uploaded_as": item["uploaded_as"],
                "source_type": item["source_type"],
                "status": "parser_unreachable",
                "detail": str(e),
            }
        if resp.status_code >= 400:
            return {
                "original_filename": item["original_filename"],
                "uploaded_as": item["uploaded_as"],
                "source_type": item["source_type"],
                "status": "failed",
                "http_status": resp.status_code,
                "detail": resp.text,
            }
        body = resp.json()
        ids: list[str] = list(body.get("parsed_node_ids") or [])
        if not ids:
            single = (
                body.get("id")
                or body.get("workbook_id")
                or body.get("script_id")
                or body.get("schedule_id")
            )
            if single:
                ids = [single]
        return {
            "original_filename": item["original_filename"],
            "uploaded_as": item["uploaded_as"],
            "source_type": item["source_type"],
            "status": body.get("status", "ok"),
            "parsed_id": ids[0] if ids else None,
            "parsed_node_ids": ids,
            "stats": body.get("stats"),
            "duration_ms": body.get("duration_ms"),
            "warnings": body.get("warnings"),
        }

    async def _dispatch_group_via_multi(
        source_type: str, group: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Send N same-source files through the parser's /parse/multi.
        Returns (per-file results, cross-file info)."""
        base = _target_url(source_type)
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{base}/parse/multi",
                    json={
                        "file_paths": [it["container_path"] for it in group],
                        "overwrite": overwrite,
                    },
                )
        except httpx.RequestError as e:
            # Fall back to per-file independent parses on a transport failure
            # — at least the user gets SOME data back rather than a 502.
            fallback = await asyncio.gather(*(_dispatch_single(it) for it in group))
            for r in fallback:
                r.setdefault("detail", f"fell back to per-file dispatch: {e}")
            return fallback, None
        if resp.status_code >= 400:
            # Parser-side error — synthesize failed entries.
            return [
                {
                    "original_filename": it["original_filename"],
                    "uploaded_as": it["uploaded_as"],
                    "source_type": source_type,
                    "status": "failed",
                    "http_status": resp.status_code,
                    "detail": resp.text,
                }
                for it in group
            ], None
        body = resp.json()
        # Map per-file results back to our auto-batch entries by container
        # path (the parser echoes file_path verbatim).
        by_path = {pf["file_path"]: pf for pf in body.get("files", [])}
        per_file: list[dict[str, Any]] = []
        for it in group:
            pf = by_path.get(it["container_path"], {})
            ids = list(pf.get("parsed_node_ids") or [])
            per_file.append({
                "original_filename": it["original_filename"],
                "uploaded_as": it["uploaded_as"],
                "source_type": source_type,
                "status": pf.get("status", "ok"),
                "parsed_id": ids[0] if ids else None,
                "parsed_node_ids": ids,
                "stats": {
                    "parsed_schedules": pf.get("parsed_schedules", 0),
                    "parsed_jobs": pf.get("parsed_jobs", 0),
                    "parse_errors": pf.get("parse_errors", 0),
                },
                "duration_ms": None,
                "warnings": pf.get("warnings"),
            })
        # Roll the cross-file commonality out to the top-level response so
        # the frontend can show "1 cross-file connection found" chips.
        commonality = body.get("commonality") or {}
        cross_info = {
            "source_type": source_type,
            "shared_entity_types": list(
                (commonality.get("shared_entities") or {}).keys()
            ),
            "cross_file_follows": commonality.get("cross_file_follows") or [],
            "merged_stats": body.get("merged_stats") or {},
        }
        return per_file, cross_info

    # Partition into (unsupported, per_source_groups)
    unsupported_items = [it for it in items if it["source_type"] is None]
    groups: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        if it["source_type"] is None:
            continue
        groups.setdefault(it["source_type"], []).append(it)

    # Dispatch every group concurrently. Each group is one task:
    #   - multi-capable + ≥2 files  → /parse/multi
    #   - everything else            → per-file /parse
    async def _dispatch_group(
        source_type: str, group: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if source_type in MULTI_CAPABLE_SOURCES and len(group) >= 2:
            return await _dispatch_group_via_multi(source_type, group)
        results = await asyncio.gather(*(_dispatch_single(it) for it in group))
        return list(results), None

    group_tasks = [
        _dispatch_group(st, gr) for st, gr in groups.items()
    ]
    group_results = await asyncio.gather(*group_tasks)

    # Flatten back to the original per-file ordering so the UI keeps the
    # user's upload order. We index by uploaded_as.
    results_by_key: dict[str, dict[str, Any]] = {}
    cross_files_info: list[dict[str, Any]] = []
    for per_file, cross in group_results:
        for r in per_file:
            results_by_key[r["uploaded_as"]] = r
        if cross is not None:
            cross_files_info.append(cross)
    for it in unsupported_items:
        results_by_key[it["uploaded_as"]] = _unsupported(it)

    results = [results_by_key[it["uploaded_as"]] for it in items]

    # Summary counts — useful for the frontend to render quick chips.
    summary: dict[str, int] = {}
    for r in results:
        key = r.get("source_type") or "unsupported"
        summary[key] = summary.get(key, 0) + 1

    overall_status = "ok"
    if any(r["status"] == "failed" for r in results):
        overall_status = "partial"
    if all(r["status"] in {"failed", "unsupported", "parser_unreachable"} for r in results):
        overall_status = "failed"

    # If the caller asked to group these files into a project, look up /
    # create the project by name and attach every successfully-parsed
    # file. We do this AFTER dispatch so unsupported / failed files don't
    # pollute the project. The project_files table de-dupes via PK.
    project_payload: dict[str, Any] | None = None
    if project_name:
        from .postgres_client import get_pool as _get_pool
        from .projects import (
            ProjectFileRef,
            attach_files_to_project,
            get_or_create_project,
        )

        pool = _get_pool()
        if pool is None:
            project_payload = {
                "requested_name": project_name,
                "error": "Postgres unreachable — files parsed but not grouped",
            }
        else:
            try:
                project_id = await get_or_create_project(pool, project_name)
                refs = [
                    ProjectFileRef(
                        neo4j_id=node_id,
                        source_type=r["source_type"],
                        file_name=r["original_filename"],
                    )
                    for r in results
                    if r.get("status") == "ok" and r.get("source_type")
                    for node_id in (r.get("parsed_node_ids") or [])
                ]
                file_count = await attach_files_to_project(pool, project_id, refs)
                project_payload = {
                    "id": project_id,
                    "name": project_name.strip(),
                    "attached_file_count": file_count,
                }
            except HTTPException as e:
                project_payload = {
                    "requested_name": project_name,
                    "error": str(e.detail),
                    "status_code": e.status_code,
                }

    return {
        "status": overall_status,
        "batch_uuid": batch_uuid,
        "files": results,
        "summary": summary,
        "project": project_payload,
        # Cross-file analysis: one entry per multi-capable source-type group
        # that had ≥2 files in this batch. Tells the user which cross-file
        # connections the parser actually resolved between their files.
        "cross_file_analysis": cross_files_info,
    }


@router.get("/parsers/health")
async def parser_health() -> dict[str, str]:
    """Quick liveness check across every parser. Each entry is 'ok'/'unreachable'."""
    s = get_settings()
    targets = {
        "tableau": s.parser_tableau_url,
        "tws": s.parser_tws_url,
        "qlikview": s.parser_qlikview_url,
        "spark": s.parser_spark_url,
    }
    out: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, url in targets.items():
            try:
                r = await client.get(f"{url}/health")
                out[name] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
            except httpx.RequestError:
                out[name] = "unreachable"
    return out
