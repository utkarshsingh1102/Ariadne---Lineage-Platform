"""Phase 7 — golden-manifest regression suite for the v0.2 lineage stress fixture.

This is the single integration test that locks in every Phase 2–5 invariant
against the stress fixture. If it breaks, something downstream broke — read
the failure, don't 'fix' the test until the invariant is restored.

The suite has three sections:

  1. Topology counts — workstations / streams / jobs / etc.
  2. Marquee resolution invariants — VALIDATE collision, conditional fork,
     recovery, event triggers.
  3. Robustness invariants — malformed-input never returns ok; workstation-
     only files return ok with 0 schedules.
"""
from __future__ import annotations

from collections import Counter

import pytest


GOLDEN = {
    "workstations": 4,    # ETL_AGENT_01, ETL_AGENT_02, DB_AGENT_01, MASTER_DM
    "job_streams":  5,    # INGESTION, TRANSFORM_SPARK, WAREHOUSE_LOAD, RECONCILE, DR_FAILOVER
    "schedules":    5,    # one per stream
    "jobs":        17,    # plan said 16 but actual count is 17 (incl. CLEANUP_LANDING + LOAD_ROLLBACK + DR_REPLICATE)
    "calendars":    2,    # BANK_WORKDAYS, MONTH_END
    "resources":    3,    # DB_CONN_POOL, SPARK_SLOTS, REDSHIFT_LOAD_LOCK
    "prompts":      2,    # RECON_SIGNOFF, DR_FAILOVER_OK
    "event_rules":  1,    # DR_TRIGGER_RULE
}


@pytest.fixture
def stress(fixture_path):
    """Parse + resolve the stress fixture per test (parsing is ~30 ms, cheap)."""
    from tws_parser.parser.composer import parse_composer_full_with_errors
    from tws_parser.parser.dependencies import resolve_full

    unit, errors = parse_composer_full_with_errors(str(fixture_path("09_lineage_stress.txt")))
    deps = resolve_full(unit)
    return {"unit": unit, "deps": deps, "errors": errors}


# ---------------------------------------------------------------------------
# 1. Topology counts
# ---------------------------------------------------------------------------


def test_zero_parse_errors_on_stress_fixture(stress):
    """v0.2 grammar must parse the stress fixture cleanly — no swallowed errors."""
    assert stress["errors"] == [], (
        "expected zero parse errors; got:\n  "
        + "\n  ".join(f"line {e.line}:{e.column} {e.msg}" for e in stress["errors"])
    )


def test_topology_counts_match_golden_manifest(stress):
    unit = stress["unit"]
    actual = {
        "workstations": len(unit.workstations),
        "job_streams": len(unit.job_streams),
        "schedules": len(unit.schedules),
        "jobs": sum(len(s.jobs) for s in unit.schedules),
        "calendars": len(unit.calendars),
        "resources": len(unit.resources),
        "prompts": len(unit.prompts),
        "event_rules": len(unit.event_rules),
    }
    assert actual == GOLDEN, f"topology drift: {actual} vs {GOLDEN}"


# ---------------------------------------------------------------------------
# 2. Marquee resolution invariants
# ---------------------------------------------------------------------------


def test_two_validate_jobs_remain_distinct(stress):
    """Identity / collision — the spec invariant. ``VALIDATE`` in INGESTION
    must NOT hash the same as ``VALIDATE`` in RECONCILE.
    """
    unit = stress["unit"]
    validates = {
        j.qualified_name: j
        for s in unit.schedules for j in s.jobs if j.name == "VALIDATE"
    }
    assert "ETL_AGENT_01#INGESTION.VALIDATE" in validates
    assert "DB_AGENT_01#RECONCILE.VALIDATE" in validates
    assert (
        validates["ETL_AGENT_01#INGESTION.VALIDATE"].id
        != validates["DB_AGENT_01#RECONCILE.VALIDATE"].id
    )


def test_transform_landing_has_two_succ_predecessors(stress):
    """Join dependency — TRANSFORM_LANDING FOLLOWS EXTRACT_ORDERS IF SUCC
    AND EXTRACT_CUSTOMERS IF SUCC. In FollowsEdge terms: TL is the
    ``from`` end (the dependent), and EXTRACT_* are the ``to`` ends
    (the predecessors).
    """
    deps = stress["deps"]
    from_tl = [
        e for e in deps.follows_edges
        if e.from_qualified == "ETL_AGENT_01#INGESTION.TRANSFORM_LANDING"
    ]
    assert len(from_tl) == 2
    targets = sorted(e.to_qualified for e in from_tl)
    assert targets == [
        "ETL_AGENT_01#INGESTION.EXTRACT_CUSTOMERS",
        "ETL_AGENT_01#INGESTION.EXTRACT_ORDERS",
    ]
    assert all(e.condition == "SUCC" for e in from_tl)


def test_conditional_fork_rc_0_and_rc_4_distinct_edges(stress):
    """QUALITY_GATE follows TRANSFORM_LANDING IF RC=0 and QUALITY_QUARANTINE
    IF RC=4 — two distinct conditional edges. QG/QQ are the dependents
    (``from`` end); TL is the shared predecessor (``to`` end).
    """
    deps = stress["deps"]
    follow_tl = [
        e for e in deps.follows_edges
        if e.to_qualified == "ETL_AGENT_01#INGESTION.TRANSFORM_LANDING"
    ]
    rc_pairs = sorted(
        (e.from_qualified.rsplit(".", 1)[-1], e.condition)
        for e in follow_tl
        if e.condition and e.condition.startswith("RC=")
    )
    assert ("QUALITY_GATE", "RC=0") in rc_pairs
    assert ("QUALITY_QUARANTINE", "RC=4") in rc_pairs


def test_success_failure_merge_into_refresh_views(stress):
    """REFRESH_VIEWS follows LOAD_FACT IF SUCC and LOAD_DIM IF ABEND —
    a success/failure fork merging into one job. RV is the dependent
    (``from`` end); LF/LD are the predecessors (``to`` end).
    """
    deps = stress["deps"]
    from_rv = [
        e for e in deps.follows_edges
        if e.from_qualified == "DB_AGENT_01#WAREHOUSE_LOAD.REFRESH_VIEWS"
    ]
    pairs = sorted((e.to_qualified.rsplit(".", 1)[-1], e.condition) for e in from_rv)
    assert ("LOAD_DIM", "ABEND") in pairs
    assert ("LOAD_FACT", "SUCC") in pairs


def test_spark_aggregate_resolves_to_ingestion_validate_not_reconcile(stress):
    """Cross-stream + cross-workstation FOLLOWS — the marquee resolution
    test. ``SPARK_AGGREGATE FOLLOWS ETL_AGENT_01#INGESTION.VALIDATE`` must
    resolve to INGESTION's VALIDATE, NOT RECONCILE's.
    """
    deps = stress["deps"]
    unit = stress["unit"]
    spark = next(j for s in unit.schedules for j in s.jobs if j.name == "SPARK_AGGREGATE")
    edges = [e for e in deps.follows_edges if e.from_job_id == spark.id]
    assert len(edges) == 1
    target = edges[0]
    assert target.to_qualified == "ETL_AGENT_01#INGESTION.VALIDATE"
    # And the id specifically points at INGESTION, not RECONCILE.
    validates = {
        j.qualified_name: j
        for s in unit.schedules for j in s.jobs if j.name == "VALIDATE"
    }
    assert target.to_job_id == validates["ETL_AGENT_01#INGESTION.VALIDATE"].id
    assert target.to_job_id != validates["DB_AGENT_01#RECONCILE.VALIDATE"].id


def test_recovery_edges_resolved(stress):
    """RECOVERY AFTER targets are resolved + edges emitted."""
    deps = stress["deps"]
    pairs = {(e.from_qualified, e.to_qualified) for e in deps.recovery_edges}
    assert ("ETL_AGENT_01#INGESTION.TRANSFORM_LANDING",
            "ETL_AGENT_01#INGESTION.CLEANUP_LANDING") in pairs
    assert ("DB_AGENT_01#WAREHOUSE_LOAD.LOAD_FACT",
            "DB_AGENT_01#WAREHOUSE_LOAD.LOAD_ROLLBACK") in pairs


def test_resource_quantities_preserved(stress):
    """SPARK_AGGREGATE + SPARK_ENRICH NEEDS 2 SPARK_SLOTS each; LOAD_DIM +
    LOAD_FACT NEEDS 1 REDSHIFT_LOAD_LOCK each."""
    deps = stress["deps"]
    by_res = Counter()
    for e in deps.requires_resource_edges:
        by_res[(e.resource_name, e.quantity)] += 1
    assert by_res[("SPARK_SLOTS", 2)] == 2
    assert by_res[("REDSHIFT_LOAD_LOCK", 1)] == 2
    assert by_res[("DB_CONN_POOL", 1)] == 1


def test_prompt_gates_resolved(stress):
    """LOAD_FACT and RECONCILE.VALIDATE both WAITS_FOR_PROMPT RECON_SIGNOFF."""
    deps = stress["deps"]
    waiting_on_recon = [
        e for e in deps.waits_for_prompt_edges if e.prompt_name == "RECON_SIGNOFF"
    ]
    assert len(waiting_on_recon) == 2


def test_event_rule_triggers_dr_failover_stream(stress):
    """DR_TRIGGER_RULE TRIGGERS MASTER_DM#DR_FAILOVER."""
    deps = stress["deps"]
    assert len(deps.triggers_edges) == 1
    e = deps.triggers_edges[0]
    assert e.target_stream_qualified == "MASTER_DM#DR_FAILOVER"


def test_zero_unresolved_dependencies(stress):
    """Every FOLLOWS / RECOVERY / TRIGGERS / RESOURCE / PROMPT target in the
    stress fixture resolves cleanly — the file is self-contained."""
    deps = stress["deps"]
    unresolved = [
        w for w in deps.warnings
        if w.type.startswith("unresolved_")
    ]
    assert unresolved == [], f"unexpected unresolved targets: {unresolved}"


# ---------------------------------------------------------------------------
# 3. API robustness invariants — status field invariant survives
# ---------------------------------------------------------------------------


def test_api_returns_status_ok_on_stress_fixture(
    fixture_path, monkeypatch, graph_writer_mock, rdbms_writer_mock
):
    from fastapi.testclient import TestClient
    from tws_parser.main import app

    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(fixture_path("09_lineage_stress.txt")),
        "write_neo4j": True,
        "write_postgres": True,
        "overwrite": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    # Stats expose the new v0.2 topology counts.
    stats = body["stats"]
    assert stats["workstations"] == GOLDEN["workstations"]
    assert stats["job_streams"] == GOLDEN["job_streams"]
    assert stats["calendars"] == GOLDEN["calendars"]
    assert stats["prompts"] == GOLDEN["prompts"]
    assert stats["event_rules"] == GOLDEN["event_rules"]
    # Edge counts.
    assert stats["follows_edges"] >= 10
    assert stats["recovery_edges"] == 2
    assert stats["triggers_edges"] == 1
    assert stats["scheduled_by_edges"] == 2


def test_workstation_only_fixture_returns_ok_with_zero_schedules(
    fixture_path, monkeypatch, graph_writer_mock, rdbms_writer_mock
):
    """A file containing only CPUNAME blocks must parse status=ok with
    parsed_schedules=0. The standing requirement: ok only when zero
    collected errors AND no silent drops — workstation-only IS legitimate.
    """
    from fastapi.testclient import TestClient
    from tws_parser.main import app

    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(fixture_path("11_workstation_only.txt")),
        "write_neo4j": False,
        "write_postgres": False,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["parsed_schedules"] == 0
    assert body["parsed_jobs"] == 0
    # But the workstations DID land.
    assert body["stats"]["workstations"] == 2
    assert [w for w in body["warnings"] if w["type"] == "parse_error"] == []


def test_malformed_fixture_never_returns_ok(
    fixture_path, monkeypatch, graph_writer_mock, rdbms_writer_mock
):
    """Standing requirement: parse errors must never coexist with status=ok."""
    from fastapi.testclient import TestClient
    from tws_parser.main import app

    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(fixture_path("10_malformed.txt")),
        "write_neo4j": False,
        "write_postgres": False,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in {"partial", "failed"}
    assert any(w["type"] == "parse_error" for w in body["warnings"])
