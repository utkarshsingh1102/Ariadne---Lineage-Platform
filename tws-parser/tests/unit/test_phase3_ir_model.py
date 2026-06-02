"""Phase 3 — the v0.2 IR model.

Verifies that ``parse_composer_full_with_errors`` builds WorkstationIR,
JobStreamIR, CalendarIR, PromptIR, EventRuleIR, and the new structured
JobIR fields (workstation/stream/follows_refs/recovery_after/every/prompts).
"""
from __future__ import annotations

from pathlib import Path

import pytest


STRESS_PATH = (
    Path(__file__).resolve().parents[3]
    / "tableau-improvement/TWS-improvement/tws_lineage_stress_test.txt"
)


def _full_parse(text: str):
    from tws_parser.parser.composer import parse_composer_full_with_errors

    unit, errors = parse_composer_full_with_errors(text)
    assert errors == [], (
        "expected zero parse errors; got:\n  "
        + "\n  ".join(f"line {e.line}:{e.column} {e.msg}" for e in errors)
    )
    return unit


# ---------------------------------------------------------------------------
# Per-IR construction (small focused fixtures)
# ---------------------------------------------------------------------------


def test_workstation_ir_populated_from_cpuname_block():
    unit = _full_parse(
        """
        CPUNAME ETL_AGENT_01
          DESCRIPTION "Primary ETL fault-tolerant agent"
          OS UNIX
          NODE etl01.bank.internal TCPADDR 31111
          FOR MAESTRO
            TYPE FTA
            AUTOLINK ON
            BEHINDFIREWALL OFF
        END
        """
    )
    assert len(unit.workstations) == 1
    ws = unit.workstations[0]
    assert ws.name == "ETL_AGENT_01"
    assert ws.description == "Primary ETL fault-tolerant agent"
    assert ws.os == "UNIX"
    assert ws.node == "etl01.bank.internal"
    assert ws.tcp_addr == 31111
    assert ws.type == "FTA"
    assert ws.autolink is True
    assert ws.behind_firewall is False


def test_calendar_ir_populated():
    unit = _full_parse(
        """
        CALENDAR BANK_WORKDAYS
          "Business days excluding bank holidays"
          01/02/2026 01/05/2026 01/06/2026
        """
    )
    assert len(unit.calendars) == 1
    c = unit.calendars[0]
    assert c.name == "BANK_WORKDAYS"
    assert c.description == "Business days excluding bank holidays"
    assert c.dates == ["01/02/2026", "01/05/2026", "01/06/2026"]


def test_prompt_ir_populated():
    unit = _full_parse(
        """
        PROMPT RECON_SIGNOFF
          "Operations: confirm overnight recon balanced? (Y to release)"
        """
    )
    assert len(unit.prompts) == 1
    p = unit.prompts[0]
    assert p.name == "RECON_SIGNOFF"
    assert "recon" in p.text.lower()


def test_event_rule_ir_populated():
    unit = _full_parse(
        """
        EVENTRULE DR_TRIGGER_RULE
          DESCRIPTION "Launch DR failover when trigger file lands"
          IS ACTIVE
          EVENTRULETYPE filter
          EVENT FileCreated
            NODE mdm.bank.internal
            FILENAME "/data/dr/trigger_failover.flag"
          ACTION SBS
            JOBSTREAM MASTER_DM#DR_FAILOVER
        END
        """
    )
    assert len(unit.event_rules) == 1
    er = unit.event_rules[0]
    assert er.name == "DR_TRIGGER_RULE"
    assert er.active is True
    assert er.rule_type == "filter"
    assert er.event_type == "FileCreated"
    assert er.event_node == "mdm.bank.internal"
    assert er.event_filename == "/data/dr/trigger_failover.flag"
    assert er.action_type == "SBS"
    assert er.target_stream_qualified == "MASTER_DM#DR_FAILOVER"


def test_job_stream_ir_built_alongside_schedule():
    unit = _full_parse(
        """
        SCHEDULE ETL_AGENT_01#INGESTION
          DESCRIPTION "Nightly source ingestion pipeline"
          AT 0100
          UNTIL 0500 ONUNTIL CANC
          PRIORITY 50
          LIMIT 5
          CARRYFORWARD
          :
          EXTRACT_ORDERS
            SCRIPTNAME "/opt/etl/bin/extract_orders.ksh"
            STREAMLOGON etluser
        END
        """
    )
    assert len(unit.schedules) == 1
    assert len(unit.job_streams) == 1
    stream = unit.job_streams[0]
    assert stream.workstation == "ETL_AGENT_01"
    assert stream.name == "INGESTION"
    assert stream.description == "Nightly source ingestion pipeline"
    assert stream.priority == 50
    assert stream.limit == 5
    assert stream.carry_forward is True
    assert stream.start_time == "01:00"
    assert stream.end_time == "05:00"
    assert stream.qualified_name == "ETL_AGENT_01#INGESTION"
    # Job is owned by both ScheduleIR (legacy) and JobStreamIR (v0.2).
    assert len(stream.jobs) == 1
    assert stream.jobs[0].name == "EXTRACT_ORDERS"


# ---------------------------------------------------------------------------
# JobIR — qualified identity + FollowsRef condition capture
# ---------------------------------------------------------------------------


def test_job_ir_has_qualified_workstation_and_stream():
    unit = _full_parse(
        """
        SCHEDULE ETL_AGENT_01#INGESTION
          AT 0100
          :
          VALIDATE
            SCRIPTNAME "/opt/etl/bin/v.ksh"
            STREAMLOGON u
        END
        """
    )
    job = unit.schedules[0].jobs[0]
    assert job.workstation == "ETL_AGENT_01"
    assert job.stream == "INGESTION"
    assert job.qualified_name == "ETL_AGENT_01#INGESTION.VALIDATE"


def test_two_validate_jobs_in_different_streams_have_distinct_ids():
    """The marquee invariant — both ``VALIDATE`` jobs must hash distinct."""
    unit = _full_parse(
        """
        SCHEDULE ETL_AGENT_01#INGESTION
          AT 0100
          :
          VALIDATE
            SCRIPTNAME "/a.ksh"
            STREAMLOGON u
        END
        SCHEDULE DB_AGENT_01#RECONCILE
          AT 0400
          :
          VALIDATE
            SCRIPTNAME "/b.ksh"
            STREAMLOGON u
        END
        """
    )
    assert len(unit.schedules) == 2
    job_a = unit.schedules[0].jobs[0]
    job_b = unit.schedules[1].jobs[0]
    assert job_a.name == "VALIDATE" and job_b.name == "VALIDATE"
    assert job_a.id != job_b.id
    assert job_a.workstation == "ETL_AGENT_01"
    assert job_b.workstation == "DB_AGENT_01"


def test_follows_ref_captures_succ_condition():
    unit = _full_parse(
        """
        SCHEDULE WS#STREAM
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
    job_b = unit.schedules[0].jobs[1]
    assert len(job_b.follows_refs) == 1
    ref = job_b.follows_refs[0]
    assert ref.scope == "internal"
    assert ref.condition == "SUCC"
    assert ref.target_job == "A"


def test_follows_ref_captures_rc_int_condition():
    unit = _full_parse(
        """
        SCHEDULE WS#STREAM
          AT 0100
          :
          A
            SCRIPTNAME "/a.ksh"
            STREAMLOGON u
          B
            SCRIPTNAME "/b.ksh"
            STREAMLOGON u
            FOLLOWS A IF RC=4
        END
        """
    )
    job_b = unit.schedules[0].jobs[1]
    assert job_b.follows_refs[0].condition == "RC=4"


def test_follows_ref_external_scope_for_qualified_target():
    unit = _full_parse(
        """
        SCHEDULE WS_B#STREAM_B
          AT 0100
          :
          B
            SCRIPTNAME "/b.ksh"
            STREAMLOGON u
            FOLLOWS WS_A#STREAM_A.SOME_JOB IF SUCC
        END
        """
    )
    ref = unit.schedules[0].jobs[0].follows_refs[0]
    assert ref.scope == "external"
    assert ref.target_workstation == "WS_A"
    assert ref.target_stream == "STREAM_A"
    assert ref.target_job == "SOME_JOB"
    assert ref.condition == "SUCC"


def test_recovery_after_captured():
    unit = _full_parse(
        """
        SCHEDULE WS#STREAM
          AT 0100
          :
          MAIN_JOB
            SCRIPTNAME "/main.ksh"
            STREAMLOGON u
            RECOVERY RERUN AFTER WS#STREAM.CLEANUP
          CLEANUP
            SCRIPTNAME "/c.ksh"
            STREAMLOGON u
        END
        """
    )
    main = unit.schedules[0].jobs[0]
    assert main.recovery == "RERUN"
    assert main.recovery_after == "WS#STREAM.CLEANUP"


def test_every_and_prompts_captured():
    unit = _full_parse(
        """
        SCHEDULE WS#STREAM
          AT 0100
          :
          GATED_JOB
            SCRIPTNAME "/g.ksh"
            STREAMLOGON u
            EVERY 30
            PROMPT RECON_SIGNOFF
        END
        """
    )
    j = unit.schedules[0].jobs[0]
    assert j.every == 30
    assert j.prompts == ["RECON_SIGNOFF"]


# ---------------------------------------------------------------------------
# Stress fixture — full topology counts
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not STRESS_PATH.exists(), reason="stress fixture not present")
def test_stress_fixture_full_topology_counts():
    from tws_parser.parser.composer import parse_composer_full_with_errors

    unit, errors = parse_composer_full_with_errors(str(STRESS_PATH))
    assert errors == [], f"expected zero parse errors; got {len(errors)}"
    assert len(unit.workstations) == 4
    assert len(unit.job_streams) == 5
    assert len(unit.schedules) == 5
    assert len(unit.calendars) == 2
    assert len(unit.resources) == 3
    assert len(unit.prompts) == 2
    assert len(unit.event_rules) == 1
    # Marquee collision check — both VALIDATE jobs distinct.
    validate_jobs = [
        j for s in unit.schedules for j in s.jobs if j.name == "VALIDATE"
    ]
    assert len(validate_jobs) == 2
    assert validate_jobs[0].id != validate_jobs[1].id
