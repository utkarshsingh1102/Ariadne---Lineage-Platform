from __future__ import annotations

import io
import os
import time
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from openpyxl import Workbook

from tws_parser import __contract_version__, __version__
from tws_parser.api.schemas import (
    CommonalityReportPayload,
    CrossFileFollowsPayload,
    ExcelExportRequest,
    HealthResponse,
    MultiParseRequest,
    MultiParseResponse,
    ParseRequest,
    ParseResponse,
    PerFileResult,
    SharedEntityPayload,
    VersionResponse,
    Warning,
)
from tws_parser.config import settings
from tws_parser.graph import client as graph_client
from tws_parser.graph import writer as graph_writer_module
from tws_parser.parser import orchestrator
from tws_parser.parser.dependencies import resolve_full
from tws_parser.parser.format_detector import FormatDetectionError
from tws_parser.parser.merge import compute_commonality
from tws_parser.rdbms import client as rdbms_client
from tws_parser.rdbms import writer as rdbms_writer_module
from tws_parser.utils.logging import get_logger

log = get_logger(__name__)
router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        neo = graph_client.healthcheck()
    except Exception:
        neo = False
    try:
        pg = rdbms_client.healthcheck()
    except Exception:
        pg = False
    return HealthResponse(
        status="ok",
        neo4j="connected" if neo else "unreachable",
        postgres="connected" if pg else "unreachable",
    )


@router.get("/version", response_model=VersionResponse)
def version() -> VersionResponse:
    return VersionResponse(
        parser="tws-parser",
        parser_version=__version__,
        contract_version=__contract_version__,
        version=__version__,
    )


@router.post("/parse", response_model=ParseResponse)
def parse(req: ParseRequest) -> ParseResponse:
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=400, detail=f"File not found: {req.file_path}")

    max_mb_env = os.environ.get("MAX_FILE_SIZE_MB")
    max_mb = int(max_mb_env) if max_mb_env else settings.max_file_size_mb
    size_mb = os.path.getsize(req.file_path) / (1024 * 1024)
    if size_mb > max_mb:
        raise HTTPException(status_code=400,
                            detail=f"File exceeds {max_mb} MB ({size_mb:.1f} MB).")

    started = time.perf_counter()
    try:
        unit, parse_errors = orchestrator.parse_full_with_errors(req.file_path)
        schedules = unit.schedules

        # Phase 1: fail-closed mode. Strict callers (ingestion pipelines) opt
        # into HTTP 422 on any lexer/parser diagnostic, before we touch the
        # graph or RDBMS. Unresolved-dependency warnings from `resolve()` are
        # NOT parse errors and do not gate strict mode.
        if req.strict and parse_errors:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Strict mode: collected parse errors",
                    "parse_errors": [
                        {"line": e.line, "column": e.column, "detail": e.msg}
                        for e in parse_errors
                    ],
                },
            )

        # Phase 4: resolve the full topology — follows + recovery + runs_on +
        # requires_resource + waits_for_prompt + triggers + scheduled_by +
        # opens edges, all with internal/external scope respected.
        deps = resolve_full(unit)
        # NB: dereference via module so test monkeypatch can swap the class.
        if req.write_neo4j:
            GraphWriter = graph_writer_module.GraphWriter
            gw = GraphWriter(graph_client.get_driver(), database=req.neo4j_database)
            # v0.2: write the full topology — every new node label + edge.
            # Existing tests' graph_writer_mock auto-mocks write_topology so
            # the mock fixture still works without modification.
            if hasattr(gw, "write_topology"):
                gw.write_topology(unit, deps, overwrite=req.overwrite)
            else:
                gw.write_schedules(schedules, overwrite=req.overwrite)
        if req.write_postgres:
            RDBMSWriter = rdbms_writer_module.RDBMSWriter
            pw = RDBMSWriter(schema=settings.postgres_schema)
            pw.write_schedules(schedules)
            pw.close()
    except HTTPException:
        raise
    except (FileNotFoundError, FormatDetectionError) as e:
        # Both are "we can't even get started" conditions, not server errors.
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.exception("parse_failed", file_path=req.file_path)
        raise HTTPException(status_code=500, detail=f"parse failed: {e}") from e

    duration_ms = int((time.perf_counter() - started) * 1000)
    stats: dict[str, int] = {
        "schedules": len(schedules),
        "jobs": sum(len(s.jobs) for s in schedules),
        "follows_dependencies": len(deps.job_dependencies),
        "schedule_dependencies": len(deps.schedule_dependencies),
        "resources": len(unit.resources),
        "file_watchers": len({p for s in schedules for j in s.jobs for p in j.opens}),
        "unresolved_dependencies": sum(
            1 for w in deps.warnings if w.type == "unresolved_dependency"
        ),
        "parse_errors": len(parse_errors),
        # Phase 4 — v0.2 topology counts.
        "workstations": len(unit.workstations),
        "job_streams": len(unit.job_streams),
        "calendars": len(unit.calendars),
        "prompts": len(unit.prompts),
        "event_rules": len(unit.event_rules),
        "follows_edges": len(deps.follows_edges),
        "recovery_edges": len(deps.recovery_edges),
        "triggers_edges": len(deps.triggers_edges),
        "scheduled_by_edges": len(deps.scheduled_by_edges),
    }

    # Phase 1: merge collected parse errors into the warnings list. Each
    # ANTLR diagnostic maps 1:1 to a Warning carrying line/column. Then
    # derive `status` so the API never returns ``ok`` while errors exist.
    warnings: list[Warning] = [
        Warning(type="parse_error", detail=e.msg, line=e.line, column=e.column)
        for e in parse_errors
    ] + [
        Warning(type=w.type, detail=w.detail) for w in deps.warnings
    ]

    if not parse_errors:
        status = "ok"
    elif schedules:
        status = "partial"
    else:
        status = "failed"

    return ParseResponse(
        status=status,
        parsed_schedules=stats["schedules"],
        parsed_jobs=stats["jobs"],
        stats=stats,
        duration_ms=duration_ms,
        warnings=warnings,
        parsed_node_ids=[s.id for s in schedules],
    )


@router.post("/parse/batch", response_model=list[ParseResponse])
def parse_batch(reqs: list[ParseRequest]) -> list[ParseResponse]:
    return [parse(r) for r in reqs]


@router.post("/parse/multi", response_model=MultiParseResponse)
def parse_multi(req: MultiParseRequest) -> MultiParseResponse:
    """Parse N TWS composer files, merge their IRs, resolve dependencies
    across the union, and report which entities + FOLLOWS edges are
    shared between files.

    Returns per-file stats + merged stats + commonality report. When
    ``write_neo4j=True``, the merged unit is written once with file
    provenance attached to every node's ``source_files`` property —
    enabling later graph queries to scope by file.
    """
    for fp in req.file_paths:
        if not os.path.exists(fp):
            raise HTTPException(status_code=400, detail=f"File not found: {fp}")

    started = time.perf_counter()
    try:
        merged, errors_by_file, provenance, merge_warnings = (
            orchestrator.parse_multi_with_errors(req.file_paths)
        )
    except (FileNotFoundError, FormatDetectionError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.exception("parse_multi_failed", file_paths=req.file_paths)
        raise HTTPException(status_code=500, detail=f"parse failed: {e}") from e

    # Strict mode: fail the whole multi-parse if any file has errors.
    total_errors = sum(len(errs) for errs in errors_by_file.values())
    if req.strict and total_errors:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Strict mode: collected parse errors in multi-parse",
                "per_file": {
                    fp: [{"line": e.line, "column": e.column, "detail": e.msg}
                         for e in errs]
                    for fp, errs in errors_by_file.items() if errs
                },
            },
        )

    # Resolve the merged unit so cross-file FOLLOWS become real edges.
    deps = resolve_full(merged)

    # Single Neo4j write with provenance attached.
    if req.write_neo4j:
        GraphWriter = graph_writer_module.GraphWriter
        gw = GraphWriter(graph_client.get_driver(), database=req.neo4j_database)
        if hasattr(gw, "write_topology"):
            gw.write_topology(
                merged, deps, overwrite=req.overwrite, source_files=provenance,
            )
        else:
            gw.write_schedules(merged.schedules, overwrite=req.overwrite)

    if req.write_postgres:
        RDBMSWriter = rdbms_writer_module.RDBMSWriter
        pw = RDBMSWriter(schema=settings.postgres_schema)
        pw.write_schedules(merged.schedules)
        pw.close()

    duration_ms = int((time.perf_counter() - started) * 1000)

    # Per-file results
    per_file: list[PerFileResult] = []
    for fp in req.file_paths:
        errs = errors_by_file.get(fp, [])
        per_file_warnings = [
            Warning(type="parse_error", detail=e.msg, line=e.line, column=e.column)
            for e in errs
        ]
        # Count what this file solely contributed at the schedule/job level.
        # Easiest read: re-parse single-file stats from the file's unresolved
        # contribution. Cheaper: derive via provenance.
        file_schedule_ids = [
            sc.id for sc in merged.schedules if fp in provenance.get(sc.id, [])
        ]
        file_schedules = len(file_schedule_ids)
        file_jobs = sum(
            1 for js in merged.job_streams for j in js.jobs
            if fp in provenance.get(j.id, [])
        )
        if errs and (file_schedules or file_jobs):
            file_status = "partial"
        elif errs:
            file_status = "failed"
        else:
            file_status = "ok"
        per_file.append(PerFileResult(
            file_path=fp, status=file_status,
            parsed_schedules=file_schedules,
            parsed_jobs=file_jobs,
            parse_errors=len(errs),
            warnings=per_file_warnings,
            parsed_node_ids=file_schedule_ids,
        ))

    # Merged stats — full topology of the union.
    merged_jobs = sum(len(js.jobs) for js in merged.job_streams)
    merged_stats = {
        "schedules": len(merged.schedules),
        "jobs": merged_jobs,
        "workstations": len(merged.workstations),
        "job_streams": len(merged.job_streams),
        "calendars": len(merged.calendars),
        "prompts": len(merged.prompts),
        "event_rules": len(merged.event_rules),
        "resources": len(merged.resources),
        "follows_edges": len(deps.follows_edges),
        "recovery_edges": len(deps.recovery_edges),
        "triggers_edges": len(deps.triggers_edges),
        "scheduled_by_edges": len(deps.scheduled_by_edges),
        "files": len(req.file_paths),
    }

    # Commonality
    report = compute_commonality(merged, provenance, deps, req.file_paths)
    commonality_payload = CommonalityReportPayload(
        shared_entities={
            label: [SharedEntityPayload(
                id=e.id, name=e.name, label=e.label, source_files=e.source_files,
            ) for e in items]
            for label, items in report.shared_entities.items()
        },
        file_specific=report.file_specific,
        cross_file_follows=[
            CrossFileFollowsPayload(
                from_file=cf.from_file,
                from_job_qualified=cf.from_job_qualified,
                to_file=cf.to_file,
                to_job_qualified=cf.to_job_qualified,
                condition=cf.condition,
            ) for cf in report.cross_file_follows
        ],
    )

    # Overall status: failed if any file failed; partial if any errors or
    # any merge-time warnings; ok otherwise.
    if any(p.status == "failed" for p in per_file):
        overall = "failed"
    elif total_errors or merge_warnings:
        overall = "partial"
    else:
        overall = "ok"

    # Top-level warnings = merge warnings + unresolved-dependency warnings
    # from the merged resolution. Per-file parse errors live in per-file results.
    top_warnings = [
        Warning(type=w.type, detail=w.detail) for w in merge_warnings
    ] + [
        Warning(type=w.type, detail=w.detail) for w in deps.warnings
    ]

    return MultiParseResponse(
        status=overall,
        files=per_file,
        merged_stats=merged_stats,
        commonality=commonality_payload,
        duration_ms=duration_ms,
        warnings=top_warnings,
    )


# ---------------------------------------------------------------------------
# Excel export — body shape: {"filter": {"start_time_min": "05:30", ...}}
# ---------------------------------------------------------------------------

@router.post("/export/excel")
def export_excel(req: ExcelExportRequest) -> StreamingResponse:
    f = req.filter
    where: list[str] = []
    params: dict[str, Any] = {}
    if f.schedule_id:
        where.append("schedule_id = %(schedule_id)s")
        params["schedule_id"] = f.schedule_id
    if f.workstation:
        where.append("workstation = %(workstation)s")
        params["workstation"] = f.workstation
    if f.start_time_min:
        where.append("start_time >= %(start_time_min)s")
        params["start_time_min"] = f.start_time_min
    if f.start_time_max:
        where.append("start_time < %(start_time_max)s")
        params["start_time_max"] = f.start_time_max
    if f.script_path_like:
        where.append("script_path ILIKE %(script_path_like)s")
        params["script_path_like"] = f.script_path_like

    where_clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (f"SELECT job_name, schedule_name, workstation, script_path, "
           f"start_time, end_time FROM {settings.postgres_schema}.v_runtime_window"
           f"{where_clause} ORDER BY start_time NULLS LAST, schedule_name, job_name")

    wb = Workbook()
    ws = wb.active
    ws.title = "TWS Jobs"
    ws.append(["job_name", "schedule_name", "workstation", "script_path",
               "start_time", "end_time"])

    try:
        with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            for row in cur.fetchall():
                ws.append([
                    row[0], row[1], row[2], row[3],
                    row[4].isoformat() if row[4] else "",
                    row[5].isoformat() if row[5] else "",
                ])
    except Exception:
        log.exception("excel_export_postgres_unreachable")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="tws-jobs.xlsx"'},
    )


try:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # type: ignore

    @router.get("/metrics")
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
except ImportError:  # pragma: no cover
    pass


def _dsn() -> str:
    return (
        f"host={settings.postgres_host} port={settings.postgres_port} "
        f"dbname={settings.postgres_db} user={settings.postgres_user} "
        f"password={settings.postgres_password}"
    )
