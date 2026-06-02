"""Phase 4 — full topology resolution.

Locks in the marquee invariants:
* internal FOLLOWS resolves in-stream only (collision-safe)
* external FOLLOWS resolves against the qualified tuple only
* RECOVERY AFTER resolution emits RecoveryEdge
* EventRule TRIGGERS resolves to JobStream id
* unresolved targets surface as ``unresolved_dependency`` (or similar)
  warnings — never silently dropped
"""
from __future__ import annotations

from pathlib import Path

import pytest


STRESS_PATH = (
    Path(__file__).resolve().parents[3]
    / "tableau-improvement/TWS-improvement/tws_lineage_stress_test.txt"
)


def _resolve(text: str):
    from tws_parser.parser.composer import parse_composer_full_with_errors
    from tws_parser.parser.dependencies import resolve_full

    unit, errors = parse_composer_full_with_errors(text)
    assert errors == [], f"unexpected parse errors: {errors}"
    return unit, resolve_full(unit)


# ---------------------------------------------------------------------------
# Internal vs external scope
# ---------------------------------------------------------------------------


def test_internal_follows_resolves_in_stream_only():
    """Bare ``FOLLOWS A`` in stream X must resolve A in stream X."""
    unit, deps = _resolve(
        """
        SCHEDULE WS#STREAM_X
          AT 0100
          :
          A
            SCRIPTNAME "/a.ksh"
            STREAMLOGON u
          B
            SCRIPTNAME "/b.ksh"
            STREAMLOGON u
            FOLLOWS A IF SUCC
        END
        """
    )
    assert len(deps.follows_edges) == 1
    edge = deps.follows_edges[0]
    assert edge.scope == "internal"
    assert edge.condition == "SUCC"
    job_a = unit.schedules[0].jobs[0]
    job_b = unit.schedules[0].jobs[1]
    assert edge.from_job_id == job_b.id
    assert edge.to_job_id == job_a.id


def test_internal_follows_never_falls_back_to_other_stream():
    """Bare ``FOLLOWS A`` MUST NOT match a job named A in a DIFFERENT stream.

    This is the collision trap from the plan — if internal resolution
    fell back to other streams, the two VALIDATE jobs would merge.
    """
    unit, deps = _resolve(
        """
        SCHEDULE WS_A#STREAM_X
          AT 0100
          :
          TARGET_JOB
            SCRIPTNAME "/x.ksh"
            STREAMLOGON u
        END
        SCHEDULE WS_B#STREAM_Y
          AT 0200
          :
          DEPENDENT
            SCRIPTNAME "/d.ksh"
            STREAMLOGON u
            FOLLOWS TARGET_JOB IF SUCC
        END
        """
    )
    # No follows edge should be emitted (cross-stream bare lookup forbidden).
    assert deps.follows_edges == []
    # Instead, an unresolved-dependency warning must fire.
    unresolved = [w for w in deps.warnings if w.type == "unresolved_dependency"]
    assert any("TARGET_JOB" in w.detail for w in unresolved)


def test_external_qualified_follows_resolves_across_streams():
    """``WS#STREAM.JOB`` resolves to the exact qualified target."""
    unit, deps = _resolve(
        """
        SCHEDULE WS_A#STREAM_X
          AT 0100
          :
          VALIDATE
            SCRIPTNAME "/a.ksh"
            STREAMLOGON u
        END
        SCHEDULE WS_B#STREAM_Y
          AT 0200
          :
          DEPENDENT
            SCRIPTNAME "/d.ksh"
            STREAMLOGON u
            FOLLOWS WS_A#STREAM_X.VALIDATE IF SUCC
        END
        """
    )
    assert len(deps.follows_edges) == 1
    e = deps.follows_edges[0]
    assert e.scope == "external"
    assert e.from_qualified == "WS_B#STREAM_Y.DEPENDENT"
    assert e.to_qualified == "WS_A#STREAM_X.VALIDATE"
    assert e.condition == "SUCC"


def test_marquee_validate_collision_resolves_correctly():
    """Two ``VALIDATE`` jobs in different streams + external dep into one of them.

    The dep must resolve to the INGESTION VALIDATE, NOT the RECONCILE one.
    """
    unit, deps = _resolve(
        """
        SCHEDULE ETL_AGENT_01#INGESTION
          AT 0100
          :
          VALIDATE
            SCRIPTNAME "/i.ksh"
            STREAMLOGON u
        END
        SCHEDULE DB_AGENT_01#RECONCILE
          AT 0400
          :
          VALIDATE
            SCRIPTNAME "/r.ksh"
            STREAMLOGON u
        END
        SCHEDULE ETL_AGENT_02#TRANSFORM_SPARK
          AT 0200
          :
          SPARK_AGGREGATE
            SCRIPTNAME "/s.ksh"
            STREAMLOGON u
            FOLLOWS ETL_AGENT_01#INGESTION.VALIDATE IF SUCC
        END
        """
    )
    spark = [j for s in unit.schedules for j in s.jobs if j.name == "SPARK_AGGREGATE"][0]
    edges = [e for e in deps.follows_edges if e.from_job_id == spark.id]
    assert len(edges) == 1
    # The to-target must be the INGESTION VALIDATE, not RECONCILE's.
    validates = {
        j.qualified_name: j
        for s in unit.schedules for j in s.jobs if j.name == "VALIDATE"
    }
    target_id = edges[0].to_job_id
    assert target_id == validates["ETL_AGENT_01#INGESTION.VALIDATE"].id
    assert target_id != validates["DB_AGENT_01#RECONCILE.VALIDATE"].id


# ---------------------------------------------------------------------------
# Conditional fork & success/failure merge
# ---------------------------------------------------------------------------


def test_two_predecessors_with_different_rc_conditions_distinct_edges():
    """RC=0 vs RC=4 on the same predecessor produce TWO distinct edges."""
    unit, deps = _resolve(
        """
        SCHEDULE WS#STREAM_X
          AT 0100
          :
          P
            SCRIPTNAME "/p.ksh"
            STREAMLOGON u
          OK
            SCRIPTNAME "/ok.ksh"
            STREAMLOGON u
            FOLLOWS P IF RC=0
          WARN
            SCRIPTNAME "/warn.ksh"
            STREAMLOGON u
            FOLLOWS P IF RC=4
        END
        """
    )
    edges_from_p = [e for e in deps.follows_edges if e.to_qualified.endswith(".P")]
    # Two edges into P: one from OK with RC=0, one from WARN with RC=4.
    assert len(edges_from_p) == 2
    conditions = sorted(e.condition for e in edges_from_p)
    assert conditions == ["RC=0", "RC=4"]


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


def test_recovery_after_emits_recovery_edge():
    unit, deps = _resolve(
        """
        SCHEDULE WS#STREAM_X
          AT 0100
          :
          MAIN
            SCRIPTNAME "/m.ksh"
            STREAMLOGON u
            RECOVERY RERUN AFTER WS#STREAM_X.CLEANUP
          CLEANUP
            SCRIPTNAME "/c.ksh"
            STREAMLOGON u
        END
        """
    )
    assert len(deps.recovery_edges) == 1
    e = deps.recovery_edges[0]
    main = unit.schedules[0].jobs[0]
    cleanup = unit.schedules[0].jobs[1]
    assert e.from_job_id == main.id
    assert e.to_recovery_job_id == cleanup.id
    assert e.recovery_action == "RERUN"


# ---------------------------------------------------------------------------
# Topology edges (runs_on, requires_resource, waits_for_prompt, scheduled_by)
# ---------------------------------------------------------------------------


def test_runs_on_edge_per_job():
    unit, deps = _resolve(
        """
        SCHEDULE WS_A#STREAM_X
          AT 0100
          :
          A
            SCRIPTNAME "/a.ksh"
            STREAMLOGON u
          B
            SCRIPTNAME "/b.ksh"
            STREAMLOGON u
        END
        """
    )
    assert len(deps.runs_on_edges) == 2
    # Both edges point at the same workstation_id (WS_A).
    assert len({e.workstation_id for e in deps.runs_on_edges}) == 1


def test_requires_resource_edge_with_quantity():
    unit, deps = _resolve(
        """
        RESOURCE ETL_AGENT_01#SPARK_SLOTS 4
          "Spark executor slots"
        SCHEDULE ETL_AGENT_01#STREAM_X
          AT 0100
          :
          JOB_A
            SCRIPTNAME "/a.ksh"
            STREAMLOGON u
            NEEDS 2 ETL_AGENT_01#SPARK_SLOTS
        END
        """
    )
    assert len(deps.requires_resource_edges) == 1
    e = deps.requires_resource_edges[0]
    assert e.quantity == 2
    assert e.resource_name == "SPARK_SLOTS"


def test_waits_for_prompt_edge():
    unit, deps = _resolve(
        """
        PROMPT RECON_SIGNOFF
          "Confirm reconciliation balanced"
        SCHEDULE WS#STREAM_X
          AT 0100
          :
          J
            SCRIPTNAME "/j.ksh"
            STREAMLOGON u
            PROMPT RECON_SIGNOFF
        END
        """
    )
    assert len(deps.waits_for_prompt_edges) == 1
    assert deps.waits_for_prompt_edges[0].prompt_name == "RECON_SIGNOFF"


def test_scheduled_by_edge_from_calendar_reference():
    unit, deps = _resolve(
        """
        CALENDAR BANK_WORKDAYS
          "Business days"
          01/02/2026 01/05/2026
        SCHEDULE WS#STREAM_X
          ON RUNCYCLE WORKDAY_RC CALENDAR BANK_WORKDAYS
          AT 0100
          :
          A
            SCRIPTNAME "/a.ksh"
            STREAMLOGON u
        END
        """
    )
    assert len(deps.scheduled_by_edges) == 1
    e = deps.scheduled_by_edges[0]
    assert e.calendar_name == "BANK_WORKDAYS"


# ---------------------------------------------------------------------------
# Triggers (event rule → stream)
# ---------------------------------------------------------------------------


def test_triggers_edge_resolves_event_rule_to_stream():
    unit, deps = _resolve(
        """
        SCHEDULE MASTER_DM#DR_FAILOVER
          PRIORITY 90
          :
          DR_REPLICATE
            SCRIPTNAME "/dr.ksh"
            STREAMLOGON u
        END
        EVENTRULE DR_TRIGGER_RULE
          IS ACTIVE
          EVENTRULETYPE filter
          EVENT FileCreated
            NODE mdm.bank.internal
            FILENAME "/data/dr/trigger.flag"
          ACTION SBS
            JOBSTREAM MASTER_DM#DR_FAILOVER
        END
        """
    )
    assert len(deps.triggers_edges) == 1
    e = deps.triggers_edges[0]
    er = unit.event_rules[0]
    stream = unit.job_streams[0]
    assert e.event_rule_id == er.id
    assert e.job_stream_id == stream.id


# ---------------------------------------------------------------------------
# Unresolved targets surface — never silent
# ---------------------------------------------------------------------------


def test_unresolved_external_target_surfaces_warning():
    unit, deps = _resolve(
        """
        SCHEDULE WS_A#STREAM_X
          AT 0100
          :
          A
            SCRIPTNAME "/a.ksh"
            STREAMLOGON u
            FOLLOWS NOT_HERE#NOWHERE.MISSING_JOB IF SUCC
        END
        """
    )
    assert deps.follows_edges == []
    assert any(
        w.type == "unresolved_dependency" and "MISSING_JOB" in w.detail
        for w in deps.warnings
    )


# ---------------------------------------------------------------------------
# Stress fixture — full edge counts
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not STRESS_PATH.exists(), reason="stress fixture not present")
def test_stress_fixture_full_resolution():
    from tws_parser.parser.composer import parse_composer_full_with_errors
    from tws_parser.parser.dependencies import resolve_full

    unit, errors = parse_composer_full_with_errors(str(STRESS_PATH))
    assert errors == [], f"unexpected parse errors: {len(errors)}"
    deps = resolve_full(unit)

    # The marquee collision: the SPARK_AGGREGATE FOLLOWS edge must point at
    # ETL_AGENT_01#INGESTION.VALIDATE, NOT DB_AGENT_01#RECONCILE.VALIDATE.
    spark = next(
        j for s in unit.schedules for j in s.jobs if j.name == "SPARK_AGGREGATE"
    )
    spark_follows = [e for e in deps.follows_edges if e.from_job_id == spark.id]
    assert len(spark_follows) == 1
    assert spark_follows[0].to_qualified == "ETL_AGENT_01#INGESTION.VALIDATE"

    # Recovery edges: TRANSFORM_LANDING → CLEANUP_LANDING and LOAD_FACT → LOAD_ROLLBACK.
    recovery_pairs = {(e.from_qualified, e.to_qualified) for e in deps.recovery_edges}
    assert ("ETL_AGENT_01#INGESTION.TRANSFORM_LANDING",
            "ETL_AGENT_01#INGESTION.CLEANUP_LANDING") in recovery_pairs
    assert ("DB_AGENT_01#WAREHOUSE_LOAD.LOAD_FACT",
            "DB_AGENT_01#WAREHOUSE_LOAD.LOAD_ROLLBACK") in recovery_pairs

    # Conditional fork: QUALITY_GATE IF RC=0 and QUALITY_QUARANTINE IF RC=4
    # share TRANSFORM_LANDING as predecessor with different conditions.
    tl_edges = [
        e for e in deps.follows_edges
        if e.to_qualified == "ETL_AGENT_01#INGESTION.TRANSFORM_LANDING"
    ]
    conditions_into_tl = sorted(
        e.condition for e in tl_edges if e.condition and e.condition.startswith("RC=")
    )
    assert conditions_into_tl == ["RC=0", "RC=4"]

    # Event rule trigger edge resolves the DR_TRIGGER_RULE.
    assert len(deps.triggers_edges) == 1

    # Resource edges with quantity 2 for SPARK_SLOTS.
    spark_slots_edges = [
        e for e in deps.requires_resource_edges if e.resource_name == "SPARK_SLOTS"
    ]
    assert spark_slots_edges
    assert all(e.quantity == 2 for e in spark_slots_edges)
