"""
Schedule IR builder + canonical ID derivation (plan §5.4).
"""
import pytest


def test_schedule_id_format():
    from tws_parser.models.domain import ScheduleIR
    s = ScheduleIR(workstation="WS_PROD", scheduler="MASTER", name="DAILY_SALES_LOAD")
    # Plan §5.4: schedule::<workstation>::<scheduler>::<name>
    assert hasattr(s, "id") and s.id and len(s.id) == 16


def test_schedule_id_is_deterministic():
    from tws_parser.models.domain import ScheduleIR
    a = ScheduleIR(workstation="WS", scheduler="M", name="X")
    b = ScheduleIR(workstation="WS", scheduler="M", name="X")
    assert a.id == b.id


def test_schedule_id_differs_across_workstations():
    """Cross-workstation duplicate names must yield distinct IDs."""
    from tws_parser.models.domain import ScheduleIR
    a = ScheduleIR(workstation="WS_PROD", scheduler="MASTER", name="DAILY")
    b = ScheduleIR(workstation="WS_TEST", scheduler="MASTER", name="DAILY")
    assert a.id != b.id


def test_schedule_carry_forward_default_false():
    from tws_parser.models.domain import ScheduleIR
    s = ScheduleIR(workstation="WS", scheduler="M", name="X")
    assert s.carry_forward is False


def test_realistic_schedule_count(fixture_path):
    from tws_parser.parser.composer import parse_composer_text
    schedules = parse_composer_text(str(fixture_path("06_realistic_dump_many_schedules.txt")))
    assert len(schedules) == 3
