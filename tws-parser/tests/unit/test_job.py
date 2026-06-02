"""
Job IR builder tests.
"""
import pytest


def test_job_id_scoped_to_schedule():
    """Plan §5.4: job::<schedule_id>::<name>"""
    from tws_parser.models.domain import ScheduleIR, JobIR
    sched_a = ScheduleIR(workstation="WS", scheduler="M", name="A")
    sched_b = ScheduleIR(workstation="WS", scheduler="M", name="B")
    job_a = JobIR(schedule_id=sched_a.id, name="EXTRACT")
    job_b = JobIR(schedule_id=sched_b.id, name="EXTRACT")
    # Same job name across schedules → distinct IDs
    assert job_a.id != job_b.id


def test_default_recovery_is_none():
    from tws_parser.models.domain import JobIR
    j = JobIR(schedule_id="x", name="J")
    assert j.recovery is None


def test_job_with_arguments(fixture_path):
    from tws_parser.parser.composer import parse_composer_text
    schedules = parse_composer_text(str(fixture_path("06_realistic_dump_many_schedules.txt")))
    sales = next(s for s in schedules if s.name == "DAILY_SALES_LOAD")
    refresh = next(j for j in sales.jobs if j.name == "REFRESH_TABLEAU_EXTRACT")
    # SCRIPT_PATH_STRIP_ARGS=true: refresh.sh + args 'sales_dashboard'
    assert refresh.script_path.endswith("refresh.sh")
    assert "sales_dashboard" in (refresh.script_args or "")
