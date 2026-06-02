from __future__ import annotations

import os
import time

from fastapi import APIRouter, HTTPException, Response

from tableau_parser import __contract_version__, __version__
from tableau_parser.api.schemas import (
    HealthResponse,
    ParseRequest,
    ParseResponse,
    VersionResponse,
    Warning,
)
from tableau_parser.config import settings
from tableau_parser.graph import client
from tableau_parser.graph import writer as graph_writer_module
from tableau_parser.parser import workbook
from tableau_parser.utils.logging import get_logger

log = get_logger(__name__)
router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    try:
        neo4j_ok = client.healthcheck()
    except Exception:
        neo4j_ok = False
    return HealthResponse(
        status="ok",
        neo4j="connected" if neo4j_ok else "unreachable",
    )


@router.get("/version", response_model=VersionResponse)
def version() -> VersionResponse:
    return VersionResponse(
        parser="tableau-parser",
        parser_version=__version__,
        contract_version=__contract_version__,
        version=__version__,
    )


@router.post("/parse", response_model=ParseResponse)
def parse(req: ParseRequest) -> ParseResponse:
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=400, detail=f"File not found: {req.file_path}")

    # Honor MAX_FILE_SIZE_MB env var dynamically (tests monkeypatch after import).
    max_mb_env = os.environ.get("MAX_FILE_SIZE_MB")
    max_mb = int(max_mb_env) if max_mb_env else settings.max_file_size_mb
    size_mb = os.path.getsize(req.file_path) / (1024 * 1024)
    if size_mb > max_mb:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {max_mb} MB limit ({size_mb:.1f} MB).",
        )

    started = time.perf_counter()
    try:
        ir = workbook.parse_workbook(req.file_path)
        # Access GraphWriter through the module so tests can monkeypatch it.
        writer_cls = graph_writer_module.GraphWriter
        writer = writer_cls(client.get_driver(), database=req.neo4j_database)
        writer.write_workbook(ir, overwrite=req.overwrite)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.exception("parse_failed", file_path=req.file_path)
        raise HTTPException(status_code=500, detail=f"parse failed: {e}") from e

    duration_ms = int((time.perf_counter() - started) * 1000)
    log.info("parse_complete", workbook_id=ir.id, duration_ms=duration_ms, stats=ir.stats())
    return ParseResponse(
        workbook_id=ir.id,
        stats=ir.stats(),
        duration_ms=duration_ms,
        warnings=[Warning(**w) for w in ir.warnings],
    )


@router.post("/parse/batch", response_model=list[ParseResponse])
def parse_batch(reqs: list[ParseRequest]) -> list[ParseResponse]:
    return [parse(r) for r in reqs]


# Prometheus
try:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # type: ignore

    @router.get("/metrics")
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
except ImportError:  # pragma: no cover
    pass
