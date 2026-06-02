"""Unit tests for v0.2 §11 — Spark runtime correlation."""
from __future__ import annotations

from pathlib import Path

from spark_parser.models.domain import (
    DataFrameIR,
    DataFrameEdgeIR,
    SparkScriptIR,
    TableIR,
    WriteEdgeIR,
)
from spark_parser.runtime.event_log_reader import read_event_log
from spark_parser.runtime.plan_correlator import (
    correlate,
    runtime_dag_signature,
    static_dag_signature,
)
from spark_parser.runtime.spark_ui_client import SparkUIClient

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "event_logs" / "orders_etl"
)


# ---------------------------------------------------------------------------
# Event log reader
# ---------------------------------------------------------------------------

def test_event_log_reader_parses_sql_execution():
    rt = read_event_log(FIXTURE)
    assert len(rt.sql_executions) == 1
    ex = rt.sql_executions[0]
    assert ex.execution_id == 0
    assert ex.description == "saveAsTable at orders_etl.py:24"
    assert ex.duration_ms == 130000
    assert "WriteToDataSourceV2" in (ex.physical_plan or "")


def test_event_log_reader_parses_jobs_and_stages():
    rt = read_event_log(FIXTURE)
    assert len(rt.jobs) == 1
    assert rt.jobs[0].job_id == 0
    assert set(rt.jobs[0].stage_ids) == {0, 1}
    assert rt.jobs[0].sql_execution_id == 0
    stage_ids = {s.stage_id for s in rt.stages}
    assert stage_ids == {0, 1}
    one = next(s for s in rt.stages if s.stage_id == 1)
    assert one.parent_ids == [0]


def test_event_log_reader_extracts_optimizations():
    rt = read_event_log(FIXTURE)
    rules = {o.rule for o in rt.optimizations}
    assert {"PushDownPredicate", "ColumnPruning"}.issubset(rules)


def test_event_log_reader_missing_path_warns(tmp_path: Path):
    rt = read_event_log(tmp_path / "does_not_exist")
    assert any(w.type == "event_log_missing" for w in rt.warnings)
    assert rt.sql_executions == []


# ---------------------------------------------------------------------------
# Plan correlator
# ---------------------------------------------------------------------------

def _static_ir_with_write() -> SparkScriptIR:
    ir = SparkScriptIR(
        id="static01", name="orders_etl",
        file_path="/jobs/orders_etl.py", script_type="pyspark",
    )
    df = DataFrameIR(var_name="enriched", id="df01", creation_order=0)
    df.derives_from_dataframe.append(DataFrameEdgeIR(source_var="orders", via="withColumn"))
    tbl = TableIR(fully_qualified_name="prod.mart.orders_out", storage_format="delta")
    df.writes_to.append(tbl)
    df.write_edges.append(WriteEdgeIR(target=tbl, mode="overwrite", via="saveAsTable"))
    ir.dataframes.append(df)
    return ir


def test_correlate_matches_write_to_sql_execution():
    ir = _static_ir_with_write()
    rt = read_event_log(FIXTURE)
    corrs, warns = correlate(ir, rt)
    assert len(corrs) == 1
    assert corrs[0].static_node_id == "df01"
    assert corrs[0].execution_id == 0
    assert corrs[0].static_dag_signature
    assert corrs[0].runtime_dag_signature


def test_correlate_emits_warning_when_no_match():
    ir = _static_ir_with_write()
    # Add a SECOND write so there are more writes than executions in the log.
    ir.dataframes.append(DataFrameIR(
        var_name="extra", id="df02", creation_order=0,
        writes_to=[TableIR(fully_qualified_name="prod.mart.extra")],
        write_edges=[WriteEdgeIR(
            target=TableIR(fully_qualified_name="prod.mart.extra"),
            mode="overwrite", via="saveAsTable",
        )],
    ))
    rt = read_event_log(FIXTURE)
    _, warns = correlate(ir, rt)
    assert any(w.type == "runtime_correlation_missing" for w in warns)


def test_dag_signatures_are_deterministic():
    ir = _static_ir_with_write()
    rt = read_event_log(FIXTURE)
    a = static_dag_signature(ir)
    b = static_dag_signature(ir)
    c = runtime_dag_signature(rt)
    d = runtime_dag_signature(rt)
    assert a == b
    assert c == d


# ---------------------------------------------------------------------------
# Spark UI client (HTTP injected)
# ---------------------------------------------------------------------------

def _ui_responses(*, app_id: str):
    def _http(url: str):
        if url.endswith(f"/applications/{app_id}/stages"):
            return (200, [
                {"stageId": 0, "parentIds": [], "name": "Scan", "numTasks": 4},
                {"stageId": 1, "parentIds": [0], "name": "Project", "numTasks": 4},
            ])
        if url.endswith(f"/applications/{app_id}/jobs"):
            return (200, [
                {"jobId": 0, "stageIds": [0, 1], "sqlExecutionId": "0"},
            ])
        if url.endswith(f"/applications/{app_id}/sql"):
            return (200, [
                {"id": 0, "description": "saveAsTable", "duration": 12345,
                 "physicalPlan": "WriteToDataSourceV2 -> Project -> Scan"},
            ])
        return (404, None)
    return _http


def test_spark_ui_client_round_trip():
    c = SparkUIClient(base_url="https://hs.example.com", http=_ui_responses(app_id="app1"))
    rt = c.fetch_runtime("app1")
    assert len(rt.sql_executions) == 1
    assert rt.sql_executions[0].description == "saveAsTable"
    assert len(rt.stages) == 2
    assert len(rt.jobs) == 1
    assert rt.jobs[0].sql_execution_id == 0
