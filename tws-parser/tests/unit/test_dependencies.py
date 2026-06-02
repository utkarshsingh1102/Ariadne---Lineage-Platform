"""
FOLLOWS / NEEDS / OPENS resolution (plan §6 step 7 + §10.6).
Plan §10.6: 100% coverage required on parser/dependencies.py.
"""
import pytest


def test_job_follows_within_schedule(fixture_path):
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.parser.dependencies import resolve

    schedules = parse_composer_text(str(fixture_path("02_multi_job_with_follows.txt")))
    resolved = resolve(schedules)

    edges = resolved.job_dependencies
    pairs = {(e.job, e.depends_on) for e in edges}
    assert ("TRANSFORM_ORDERS", "EXTRACT_ORDERS") in pairs
    assert ("LOAD_ORDERS_TO_DW", "TRANSFORM_ORDERS") in pairs


def test_schedule_level_follows_resolved_in_batch(fixture_path):
    """Plan §6 step 7: in-batch lookup is first priority."""
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.parser.dependencies import resolve

    schedules = parse_composer_text(str(fixture_path("03_schedule_level_dependency.txt")))
    resolved = resolve(schedules)
    pairs = {(d.schedule, d.depends_on_schedule) for d in resolved.schedule_dependencies}
    assert ("DAILY_SALES_LOAD", "NIGHTLY_INFRA_CHECK") in pairs


def test_unresolved_dependency_emits_warning(fixture_path):
    """Plan §6 step 7: dangling reference + warning."""
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.parser.dependencies import resolve

    schedules = parse_composer_text(str(fixture_path("06_realistic_dump_many_schedules.txt")))
    resolved = resolve(schedules)
    # WS_FINANCE#MASTER#GL_CLOSE is not in the dump → must surface as warning
    msgs = " ".join(w.detail for w in resolved.warnings)
    assert "GL_CLOSE" in msgs


def test_forward_reference_resolved(tmp_path):
    """Plan §15: Job B FOLLOWS Job C where C is defined after B."""
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.parser.dependencies import resolve

    text = """
SCHEDULE WS#M#X
  ON RUNCYCLE EVERY_WEEKDAY VALIDFROM 01/01/2025
  AT 0530
:
  JOB_B
    SCRIPTNAME "/b.sh"
    STREAMLOGON u
    FOLLOWS JOB_C
    RECOVERY STOP

  JOB_C
    SCRIPTNAME "/c.sh"
    STREAMLOGON u
    RECOVERY STOP
END
"""
    p = tmp_path / "fwd.txt"
    p.write_text(text)
    resolved = resolve(parse_composer_text(str(p)))
    pairs = {(e.job, e.depends_on) for e in resolved.job_dependencies}
    assert ("JOB_B", "JOB_C") in pairs


def test_wildcard_expands_to_schedule_not_each_job(fixture_path):
    """Plan §15: the `.@` wildcard means 'depend on the schedule', not on each job.
    Otherwise edge count explodes."""
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.parser.dependencies import resolve

    schedules = parse_composer_text(str(fixture_path("06_realistic_dump_many_schedules.txt")))
    resolved = resolve(schedules)
    # FINANCE_RECON .@ depends on DAILY_SALES_LOAD → ONE schedule edge,
    # NOT one edge per job in DAILY_SALES_LOAD
    edges_to_sales = [d for d in resolved.schedule_dependencies
                      if d.depends_on_schedule == "DAILY_SALES_LOAD"]
    assert len(edges_to_sales) == 1
