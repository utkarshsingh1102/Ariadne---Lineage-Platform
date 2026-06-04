"""FastAPI entry point for the QlikView parser.

Implements the common parser-api.yaml contract: /parse, /parse/batch,
/health, /version. The QlikView parser writes to Neo4j only (no Postgres).
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .core import QlikViewParser

__version__ = "0.1.0"
__contract_version__ = "0.1.0"


app = FastAPI(title="qlikview-parser", version=__version__)


class ParseRequest(BaseModel):
    file_path: str = Field(..., description="Absolute path inside the container")
    neo4j_database: str | None = None
    overwrite: bool = False


def _build_parser() -> QlikViewParser:
    return QlikViewParser(
        neo4j_uri=os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("NEO4J_PASSWORD", "lineagepass"),
    )


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness + Neo4j connectivity probe."""
    status = {"status": "ok", "neo4j": "connected", "postgres": "not_applicable"}
    try:
        p = _build_parser()
        with p.driver.session() as s:
            s.run("RETURN 1").consume()
        p.close()
    except Exception:
        status["neo4j"] = "unreachable"
        status["status"] = "degraded"
    return status


@app.get("/version")
def version() -> dict[str, str]:
    return {
        "parser": "qlikview-parser",
        "parser_version": __version__,
        "contract_version": __contract_version__,
    }


@app.post("/parse")
def parse(req: ParseRequest) -> dict[str, Any]:
    path = req.file_path
    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail=f"File not found: {path}")
    max_mb = int(os.environ.get("MAX_FILE_SIZE_MB", "50"))
    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > max_mb:
        raise HTTPException(
            status_code=413, detail=f"File exceeds {max_mb} MB ({size_mb:.1f} MB)"
        )

    started = time.monotonic()
    p_inst = _build_parser()
    try:
        app_ir = p_inst.parse_qvs_file(path)
        try:
            p_inst.push_to_neo4j(app_ir)
        except Exception:
            # Neo4j may be down — surface the IR anyway so the gateway can show stats.
            pass
    finally:
        p_inst.close()
    elapsed = int((time.monotonic() - started) * 1000)

    stats = {
        "loads": len(app_ir.loads),
        "variables": len(app_ir.variables),
        "subroutines": len(app_ir.subroutines),
        "includes": len(app_ir.includes),
        "fields": sum(len(l.fields) for l in app_ir.loads),
    }
    # Return the SAME :QlikScript node id the writer actually creates so
    # callers (gateway → projects) can MATCH the node by id later. Earlier
    # versions returned ``Path(path).stem`` which broke the link from
    # project_files.neo4j_id to the real Neo4j node id (a SHA-256 hash).
    from .graph.writer import _id_short
    node_id = _id_short(f"qlik_script::{path}")
    return {
        "id": node_id,
        "parsed_node_ids": [node_id],
        "stats": stats,
        "duration_ms": elapsed,
        "warnings": [
            {"type": "parse_error", "detail": e} for e in app_ir.parse_errors
        ],
    }
