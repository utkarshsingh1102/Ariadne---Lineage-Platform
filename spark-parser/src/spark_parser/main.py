"""FastAPI entry point + format-dispatching ``parse_input``."""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import graph as graph_module                              # noqa: F401
from .graph import writer as graph_writer_module
from .input.format_detector import FormatDetectionError, detect_format
from .input.notebook import concatenate_python_cells, extract_cells
from .models.domain import SparkScriptIR, WarningIR
from .pyspark.visitor import parse_pyspark
from .sparksql.lineage import extract_lineage as extract_sql_lineage
from .utils.ids import script_id


__version__ = "0.1.0"

log = logging.getLogger(__name__)

app = FastAPI(title="spark-parser", version=__version__)


# ---------------------------------------------------------------------------
# Neo4j driver — created lazily from env at first /parse call.
# ---------------------------------------------------------------------------

_neo4j_driver = None


def _get_neo4j_driver():
    """Build (and cache) a Neo4j driver from environment variables.

    Returns ``None`` if NEO4J_URI isn't set or the driver can't be built —
    in that case writes are skipped and the parser still returns IR + stats.
    """
    global _neo4j_driver
    if _neo4j_driver is not None:
        return _neo4j_driver

    uri = os.environ.get("NEO4J_URI")
    if not uri:
        return None
    try:
        from neo4j import GraphDatabase

        user = os.environ.get("NEO4J_USER", "neo4j")
        password = os.environ.get("NEO4J_PASSWORD", "neo4j")
        _neo4j_driver = GraphDatabase.driver(uri, auth=(user, password))
    except Exception as e:
        log.warning("Could not build Neo4j driver: %s", e)
        _neo4j_driver = None
    return _neo4j_driver


class ParseRequest(BaseModel):
    file_path: str = Field(..., description="Absolute path to the input file")
    language_hint: str = "auto"
    neo4j_database: str | None = None
    overwrite: bool = False


class ParseProjectRequest(BaseModel):
    """v0.2 §1 — multi-file project parse request."""
    entry_path: str = Field(..., description="Absolute path to the entry script")
    project_root: str = Field(..., description="Absolute path of the project root")
    extra_search_paths: list[str] = Field(default_factory=list)
    max_depth: int = 10
    neo4j_database: str | None = None


class ParseWithRuntimeRequest(BaseModel):
    """v0.2 §11 — pair a single script with a Spark event-log directory."""
    file_path: str = Field(..., description="Absolute path to the script")
    event_log_path: str = Field(..., description="Path to the Spark event log dir or file")
    neo4j_database: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    status = {"status": "ok", "neo4j": "not_configured", "postgres": "not_applicable"}
    drv = _get_neo4j_driver()
    if drv is not None:
        try:
            with drv.session() as s:
                s.run("RETURN 1").consume()
            status["neo4j"] = "connected"
        except Exception:
            status["neo4j"] = "unreachable"
            status["status"] = "degraded"
    return status


@app.get("/version")
def version() -> dict[str, str]:
    return {"parser": "spark-parser", "version": __version__}


@app.post("/parse")
def parse(req: ParseRequest) -> dict[str, Any]:
    path = req.file_path
    if not os.path.exists(path):
        raise HTTPException(status_code=400, detail=f"File not found: {path}")
    max_mb = int(os.environ.get("MAX_FILE_SIZE_MB", "50"))
    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > max_mb:
        raise HTTPException(
            status_code=413, detail=f"File exceeds {max_mb} MB ({size_mb:.1f} MB)",
        )

    ir = parse_input(path)

    # Stamp parsed_at so the gateway's Files index can sort by it.
    ir.parsed_at = datetime.now(timezone.utc).isoformat()

    write_result = {"nodes_written": 0, "edges_written": 0}
    driver = _get_neo4j_driver()
    if driver is not None:
        GraphWriter = graph_writer_module.GraphWriter
        try:
            gw = GraphWriter(driver=driver)
            res = gw.write_script(ir)
            if isinstance(res, dict):
                write_result = res
        except Exception as e:
            log.warning("Neo4j write failed for %s: %s", path, e)
            ir.warnings.append(
                WarningIR(type="neo4j_write_failed", detail=str(e))
            )

    return {
        "script_id": ir.id,
        "script_type": ir.script_type,
        "stats": _stats(ir),
        "graph": write_result,
        "warnings": [
            {"type": w.type, "detail": w.detail, "line": w.line}
            for w in ir.warnings
        ],
    }


# ---------------------------------------------------------------------------
# Dispatcher used by both the API and the integration tests
# ---------------------------------------------------------------------------

@app.post("/parse/project")
def parse_project_endpoint(req: ParseProjectRequest) -> dict[str, Any]:
    """Parse a multi-file project (v0.2 §1).

    Walks imports from ``entry_path`` under ``project_root``, returns the
    aggregated project lineage plus per-module stats and the import graph.
    """
    if not os.path.exists(req.entry_path):
        raise HTTPException(status_code=400, detail=f"Entry not found: {req.entry_path}")
    if not os.path.isdir(req.project_root):
        raise HTTPException(
            status_code=400, detail=f"Project root not a directory: {req.project_root}",
        )

    from .project.project_parser import ProjectParser

    project = ProjectParser(
        project_root=req.project_root,
        extra_search_paths=req.extra_search_paths or None,
        max_depth=req.max_depth,
    ).parse(req.entry_path)

    # Stamp parsed_at across modules so the gateway can sort.
    now = datetime.now(timezone.utc).isoformat()
    for module in project.modules:
        module.parsed_at = now

    write_result: dict[str, Any] = {"nodes_written": 0, "edges_written": 0}
    driver = _get_neo4j_driver()
    if driver is not None:
        try:
            gw = graph_writer_module.GraphWriter(driver=driver)
            write_result = gw.write_project(project) or write_result
        except Exception as e:
            log.warning("Neo4j project write failed for %s: %s", req.entry_path, e)

    return {
        "entry_script_id": project.entry_script_id,
        "project_root": project.project_root,
        "modules": [
            {
                "script_id": m.id,
                "file_path": m.file_path,
                "script_type": m.script_type,
                "stats": _stats(m),
            }
            for m in project.modules
        ],
        "import_edges": [
            {
                "from_script_id": e.from_script_id,
                "to_script_id": e.to_script_id,
                "symbol": e.symbol,
                "kind": e.kind,
                "module": e.module,
                "line": e.line,
            }
            for e in project.import_edges
        ],
        "graph": write_result,
        "warnings": [
            {"type": w.type, "detail": w.detail, "line": w.line}
            for w in project.warnings
        ],
    }


@app.post("/parse/with-runtime")
def parse_with_runtime(req: ParseWithRuntimeRequest) -> dict[str, Any]:
    """Parse a script and correlate it with a Spark event log (v0.2 §11)."""
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=400, detail=f"File not found: {req.file_path}")
    if not os.path.exists(req.event_log_path):
        raise HTTPException(
            status_code=400, detail=f"Event log not found: {req.event_log_path}",
        )

    from .runtime.event_log_reader import read_event_log
    from .runtime.plan_correlator import correlate

    ir = parse_input(req.file_path)
    ir.parsed_at = datetime.now(timezone.utc).isoformat()
    runtime = read_event_log(req.event_log_path)
    correlations, runtime_warnings = correlate(ir, runtime)
    ir.warnings.extend(runtime_warnings)

    return {
        "script_id": ir.id,
        "script_type": ir.script_type,
        "stats": _stats(ir),
        "runtime": {
            "sql_executions": len(runtime.sql_executions),
            "jobs": len(runtime.jobs),
            "stages": len(runtime.stages),
            "optimizations": len(runtime.optimizations),
        },
        "correlations": [
            {
                "static_node_id": c.static_node_id,
                "execution_id": c.execution_id,
                "physical_plan": c.physical_plan,
                "static_dag_signature": c.static_dag_signature,
                "runtime_dag_signature": c.runtime_dag_signature,
            }
            for c in correlations
        ],
        "warnings": [
            {"type": w.type, "detail": w.detail, "line": w.line}
            for w in ir.warnings
        ],
    }


def parse_input(file_path: str) -> SparkScriptIR:
    """Detect format and route to the right backend, returning a unified IR."""
    p = Path(file_path)
    sid = script_id(str(p))
    try:
        fmt = detect_format(p)
    except FormatDetectionError as e:
        ir = SparkScriptIR(id=sid, name=p.stem, file_path=str(p), script_type="unknown")
        ir.warnings.append(WarningIR(type="unsupported_format", detail=str(e)))
        return ir

    if fmt == "pyspark":
        return parse_pyspark(p)

    if fmt == "sparksql":
        ir = SparkScriptIR(id=sid, name=p.stem, file_path=str(p), script_type="sparksql")
        lineage = extract_sql_lineage(p.read_text(encoding="utf-8"), dialect="spark")
        # Roll SQL lineage into a single synthetic DataFrame for the unified shape.
        from .models.domain import DataFrameIR, TableIR, WriteEdgeIR
        from .utils.ids import dataframe_id

        df = DataFrameIR(
            var_name="__sql__",
            id=dataframe_id(script_id=sid, var_name="__sql__", creation_order=0),
            from_sql_block=True,
            # Pure ``.sql`` files have no PySpark chain to anchor against,
            # so the synthetic DataFrame is itself the anchor. Without this
            # flag the writer's "skip non-anchor" guard drops it, and the
            # script's read/write edges never land in Neo4j.
            is_anchor=True,
        )
        for src in lineage.source_tables:
            df.reads_from.append(TableIR(fully_qualified_name=src, storage_format="hive"))
        for tgt in lineage.target_tables:
            tbl = TableIR(fully_qualified_name=tgt, storage_format="hive")
            df.writes_to.append(tbl)
            df.write_edges.append(WriteEdgeIR(target=tbl, mode="overwrite", via="sparksql"))
        df.derivations.extend(lineage.derivations)
        ir.dataframes.append(df)
        ir.warnings.extend(lineage.warnings)
        return ir

    if fmt in {"notebook_jupyter", "notebook_databricks", "notebook_databricks_archive"}:
        return _parse_notebook(p, fmt)

    if fmt == "scala":
        ir = SparkScriptIR(id=sid, name=p.stem, file_path=str(p), script_type="scala")
        ir.warnings.append(WarningIR(
            type="scala_out_of_scope",
            detail="Scala Spark is out of scope for v0.1 — file skipped",
        ))
        return ir

    ir = SparkScriptIR(id=sid, name=p.stem, file_path=str(p), script_type="unknown")
    ir.warnings.append(WarningIR(type="unsupported_format", detail=f"format {fmt}"))
    return ir


def _parse_notebook(path: Path, fmt: str) -> SparkScriptIR:
    """Concatenate Python cells → parse as PySpark; SQL cells → extract lineage."""
    from .models.domain import NotebookCellIR

    sid = script_id(str(path))
    cells = extract_cells(path)
    py_source = "\n\n".join(c.source for c in cells if c.language == "python")
    sql_cells = [c for c in cells if c.language == "sql"]

    if py_source.strip():
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8",
        ) as tmp:
            tmp.write(py_source)
            tmp_path = tmp.name
        ir = parse_pyspark(tmp_path)
        # Rebrand the IR with the notebook's real metadata
        ir.id = sid
        ir.file_path = str(path)
        ir.name = path.stem
        ir.script_type = "notebook"
    else:
        ir = SparkScriptIR(id=sid, name=path.stem, file_path=str(path), script_type="notebook")

    # v0.2 §2 — preserve per-cell metadata so downstream consumers can see
    # execution-order, language, and (for ipynb) the run sequence.
    for c in cells:
        ir.cells.append(NotebookCellIR(
            index=c.index, language=c.language, source=c.source,
            execution_count=c.execution_count,
        ))

    # Hidden state: out-of-order Jupyter execution. If execution_counts are
    # present and not monotonically increasing with cell index, warn.
    _check_out_of_order_execution(ir)

    # Detect %run / dbutils.notebook.run edges from the raw cell text. The
    # ProjectParser-style resolution happens later when the caller wraps this
    # notebook in a project.
    _record_notebook_run_edges(ir, cells)

    # Merge each SQL cell's lineage into a synthetic DataFrame
    for sql_cell in sql_cells:
        lineage = extract_sql_lineage(sql_cell.source, dialect="spark")
        from .models.domain import DataFrameIR, TableIR, WriteEdgeIR
        from .utils.ids import dataframe_id

        df = DataFrameIR(
            var_name=f"__sql_cell_{sql_cell.index}__",
            id=dataframe_id(
                script_id=sid,
                var_name=f"__sql_cell_{sql_cell.index}__",
                creation_order=0,
            ),
            from_sql_block=True,
            cell_index=sql_cell.index,
            # Same rationale as the .sql-file path — synthetic SQL DataFrames
            # have no PySpark anchor of their own, so promote them to anchors
            # so the writer keeps them.
            is_anchor=True,
        )
        for src in lineage.source_tables:
            df.reads_from.append(TableIR(fully_qualified_name=src, storage_format="hive"))
        for tgt in lineage.target_tables:
            tbl = TableIR(fully_qualified_name=tgt, storage_format="hive")
            df.writes_to.append(tbl)
            df.write_edges.append(WriteEdgeIR(target=tbl, mode="overwrite", via="sparksql"))
        df.derivations.extend(lineage.derivations)
        ir.dataframes.append(df)
    return ir


def _check_out_of_order_execution(ir: SparkScriptIR) -> None:
    """Warn when Jupyter cells were executed out of source order (v0.2 §2).

    The signal is the cell ``execution_count``: in a freshly-run notebook
    cells execute top-to-bottom and execution_counts go 1, 2, 3 …
    Out-of-order edits leave gaps or reorderings — that's "hidden state".
    """
    execs = [(c.index, c.execution_count) for c in ir.cells if c.execution_count is not None]
    if len(execs) < 2:
        return
    last = -1
    for idx, n in execs:
        if n < last:
            ir.warnings.append(WarningIR(
                type="hidden_state",
                subtype="out_of_order_execution",
                detail=(
                    f"Cell {idx} has execution_count={n} but a previous cell "
                    f"ran later — lineage marked partial"
                ),
            ))
            for df in ir.dataframes:
                df.lineage_partial = True
            return
        last = n


_DBX_RUN_MAGIC = "# MAGIC %run "
_DBX_RUN_MAGIC_BARE = "%run "


def _record_notebook_run_edges(ir: SparkScriptIR, cells) -> None:
    """Detect %run + dbutils.notebook.run("path", ...) and record edges (v0.2 §2)."""
    from .models.domain import NotebookRunEdgeIR

    import re as _re
    dbutils_re = _re.compile(
        r"""dbutils\.notebook\.run\s*\(\s*['"]([^'"]+)['"]""",
    )
    for c in cells:
        for raw_line in c.source.splitlines():
            line = raw_line.strip()
            # `# MAGIC %run ./shared` (Databricks .py / .ipynb magic) or
            # `%run ./shared` (Jupyter top-of-cell magic). Both forms.
            target: str | None = None
            if line.startswith(_DBX_RUN_MAGIC):
                target = line[len(_DBX_RUN_MAGIC):].strip()
            elif line.startswith(_DBX_RUN_MAGIC_BARE):
                target = line[len(_DBX_RUN_MAGIC_BARE):].strip()
            if target:
                ir.notebook_runs.append(NotebookRunEdgeIR(
                    source_script_id=ir.id,
                    target_path=target,
                    kind="magic_run",
                    source_cell_index=c.index,
                ))
                continue
        # dbutils.notebook.run(...) — match anywhere in the cell
        for m in dbutils_re.finditer(c.source):
            ir.notebook_runs.append(NotebookRunEdgeIR(
                source_script_id=ir.id,
                target_path=m.group(1),
                kind="dbutils_notebook_run",
                source_cell_index=c.index,
            ))


def _stats(ir: SparkScriptIR) -> dict[str, int]:
    source_tables = {
        (t.fully_qualified_name or t.location)
        for df in ir.dataframes for t in df.reads_from
    }
    target_tables = {
        (t.fully_qualified_name or t.location)
        for df in ir.dataframes for t in df.writes_to
    }
    joins = sum(len(df.joins) for df in ir.dataframes)
    anchors = [df for df in ir.dataframes if df.is_anchor]
    return {
        # Plan ``dataframe_collapse_plan.md`` §7 — the display layer count
        # exposes only anchor DataFrames (named vars, IO sites, temp views,
        # forks). Granular intermediates stay in ``ir.dataframes`` for
        # internal column-lineage walks but the user-visible figure drops
        # from 46 → ~12 on the test file.
        "dataframes": len(anchors),
        "dataframes_granular": len(ir.dataframes),
        "source_tables": len({s for s in source_tables if s}),
        "target_tables": len({t for t in target_tables if t}),
        "attributes": sum(len(df.fields) for df in anchors),
        "joins": joins,
        "udfs": len(ir.udfs),
        "sql_blocks": sum(
            1 for df in ir.dataframes
            if df.from_sql_block or df.var_name.startswith("__sql")
        ),
    }
