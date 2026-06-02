"""
Deterministic node IDs (plan §5.4).
"""
import pytest


def test_schedule_id():
    from tws_parser.utils.ids import schedule_id
    a = schedule_id("WS_PROD", "MASTER", "DAILY_SALES_LOAD")
    b = schedule_id("WS_PROD", "MASTER", "DAILY_SALES_LOAD")
    assert a == b
    assert len(a) == 16


def test_job_id_qualified_is_deterministic():
    """v0.2: job_id now takes (workstation, stream, name)."""
    from tws_parser.utils.ids import job_id
    a = job_id("ETL_AGENT_01", "INGESTION", "EXTRACT")
    b = job_id("ETL_AGENT_01", "INGESTION", "EXTRACT")
    assert a == b
    assert len(a) == 16


def test_job_id_collision_safety_across_streams():
    """Two jobs literally named ``VALIDATE`` in different streams must NOT
    collide — the marquee invariant from the stress fixture."""
    from tws_parser.utils.ids import job_id
    a = job_id("ETL_AGENT_01", "INGESTION", "VALIDATE")
    b = job_id("DB_AGENT_01",  "RECONCILE", "VALIDATE")
    assert a != b


def test_workstation_and_object_ids():
    from tws_parser.utils.ids import (
        calendar_id, event_rule_id, job_stream_id, prompt_id, workstation_id,
    )
    assert workstation_id("ETL_AGENT_01") == workstation_id("etl_agent_01")  # case-insens
    assert job_stream_id("ETL_AGENT_01", "INGESTION") != job_stream_id("ETL_AGENT_02", "INGESTION")
    assert calendar_id("BANK_WORKDAYS") != calendar_id("MONTH_END")
    assert prompt_id("RECON_SIGNOFF") != prompt_id("DR_FAILOVER_OK")
    assert event_rule_id("DR_TRIGGER_RULE") != event_rule_id("OTHER_RULE")


def test_script_id_lowercased():
    """Plan §5.4: Script IDs are the cross-parser merge key — must be
    lowercased on the path."""
    from tws_parser.utils.ids import script_id
    a = script_id("/Apps/AbInitio/Run.sh")
    b = script_id("/apps/abinitio/run.sh")
    assert a == b


def test_script_id_must_match_other_parsers():
    """Cross-parser contract: same canonical string as Ab Initio / Teradata."""
    from tws_parser.utils.ids import _canonical_script_string
    assert _canonical_script_string("/Apps/AbInitio/Run.sh") == "script::/apps/abinitio/run.sh"
