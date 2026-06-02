"""Phase 2 — grammar/lexer extension smoke tests.

One test per new top-level construct. Each uses ``parse_composer_text_with_errors``
to assert the new grammar accepts the input with zero parse errors.

The point of these tests is to lock in the per-construct vocabulary so a
future grammar refactor can't silently re-break (eg. CPUNAME) — they don't
yet assert IR content (Phase 3 will).
"""
from __future__ import annotations

import pytest


def _parse_clean(text: str):
    from tws_parser.parser.composer import parse_composer_text_with_errors

    schedules, errors = parse_composer_text_with_errors(text)
    assert errors == [], (
        "expected zero parse errors; got:\n  "
        + "\n  ".join(f"line {e.line}:{e.column} {e.msg}" for e in errors)
    )
    return schedules


def test_workstation_definition_parses():
    _parse_clean(
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


def test_calendar_definition_parses():
    _parse_clean(
        """
        CALENDAR BANK_WORKDAYS
          "Business days excluding bank holidays"
          01/02/2026 01/05/2026 01/06/2026
        """
    )


def test_resource_definition_parses():
    _parse_clean(
        """
        RESOURCE ETL_AGENT_01#DB_CONN_POOL 10
          "Concurrent warehouse connections"
        """
    )


def test_prompt_definition_parses():
    _parse_clean(
        """
        PROMPT RECON_SIGNOFF
          "Operations: confirm overnight recon balanced? (Y to release)"
        """
    )


def test_event_rule_definition_parses():
    _parse_clean(
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


def test_follows_if_succ_parses():
    """``FOLLOWS X IF SUCC`` captures the SUCC condition structurally."""
    schedules = _parse_clean(
        """
        SCHEDULE WS_A#STREAM_X
          AT 0100
          :
          A
            SCRIPTNAME "/opt/a.ksh"
            STREAMLOGON u
          B
            SCRIPTNAME "/opt/b.ksh"
            STREAMLOGON u
            FOLLOWS A IF SUCC
        END
        """
    )
    assert len(schedules) == 1


def test_follows_if_rc_equals_int_parses():
    """``FOLLOWS X IF RC=4`` parses (the EQ token + INT)."""
    _parse_clean(
        """
        SCHEDULE WS_A#STREAM_X
          AT 0100
          :
          A
            SCRIPTNAME "/opt/a.ksh"
            STREAMLOGON u
          B
            SCRIPTNAME "/opt/b.ksh"
            STREAMLOGON u
            FOLLOWS A IF RC=4
        END
        """
    )


def test_recovery_after_parses():
    """``RECOVERY RERUN AFTER WS#STREAM.JOB`` parses."""
    _parse_clean(
        """
        SCHEDULE WS_A#STREAM_X
          AT 0100
          :
          MAIN_JOB
            SCRIPTNAME "/opt/main.ksh"
            STREAMLOGON u
            RECOVERY RERUN AFTER WS_A#STREAM_X.CLEANUP
          CLEANUP
            SCRIPTNAME "/opt/cleanup.ksh"
            STREAMLOGON u
        END
        """
    )


def test_inline_hash_comment_no_longer_breaks_lexer():
    """Phase 2.0 fix — indented ``#--`` comments must reach HIDDEN channel."""
    _parse_clean(
        """
        SCHEDULE WS_A#STREAM_X
          AT 0100
          :
          #-- this inline comment used to break the lexer pre-Phase-2.0
          A
            SCRIPTNAME "/opt/a.ksh"
            STREAMLOGON u
        END
        """
    )


def test_workstation_hash_still_means_qualified_name_not_comment():
    """``ETL_AGENT_01#DB_CONN_POOL`` keeps HASH as qualifier — not a comment."""
    schedules = _parse_clean(
        """
        SCHEDULE WS_A#STREAM_X
          AT 0100
          :
          A
            SCRIPTNAME "/opt/a.ksh"
            STREAMLOGON u
            NEEDS 1 ETL_AGENT_01#DB_CONN_POOL
        END
        """
    )
    assert len(schedules) == 1
    assert schedules[0].jobs[0].needs == [("DB_CONN_POOL", 1)]


def test_keyword_as_identifier_via_parserId():
    """A workstation named ``ON`` (a keyword) must still parse via parserId."""
    _parse_clean(
        """
        CPUNAME ON
          OS UNIX
        END
        """
    )
