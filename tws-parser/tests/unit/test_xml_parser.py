"""
XML path tests (plan §10.3).
Must produce the SAME ScheduleIR shape as the ANTLR path for equivalent input.
"""
import pytest


def test_xml_single_schedule(fixture_path):
    from tws_parser.parser.xml_export import parse_xml
    schedules = parse_xml(str(fixture_path("07_xml_export_single.xml")))
    assert len(schedules) == 1
    s = schedules[0]
    assert s.name == "DAILY_SALES_LOAD"
    assert s.workstation == "MASTER"
    assert s.start_time == "05:30"


def test_xml_jobs_and_follows(fixture_path):
    from tws_parser.parser.xml_export import parse_xml
    schedules = parse_xml(str(fixture_path("07_xml_export_single.xml")))
    s = schedules[0]
    names = [j.name for j in s.jobs]
    assert names == ["EXTRACT_ORDERS", "TRANSFORM_ORDERS", "LOAD_ORDERS_TO_DW"]
    transform = next(j for j in s.jobs if j.name == "TRANSFORM_ORDERS")
    assert "EXTRACT_ORDERS" in transform.follows


def test_xml_schedule_level_dependencies_with_wildcard(fixture_path):
    from tws_parser.parser.xml_export import parse_xml
    schedules = parse_xml(str(fixture_path("08_xml_export_full.xml")))
    recon = next(s for s in schedules if s.name == "FINANCE_RECON")
    assert len(recon.schedule_follows) == 2
    targets = {(d.target_workstation, d.target_schedule) for d in recon.schedule_follows}
    assert ("WS_PROD", "DAILY_SALES_LOAD") in targets
    assert ("WS_FINANCE", "GL_CLOSE") in targets
    assert all(d.wildcard for d in recon.schedule_follows)


def test_xml_needs_resource(fixture_path):
    from tws_parser.parser.xml_export import parse_xml
    schedules = parse_xml(str(fixture_path("08_xml_export_full.xml")))
    sales = next(s for s in schedules if s.name == "DAILY_SALES_LOAD")
    load = next(j for j in sales.jobs if j.name == "LOAD_ORDERS_TO_DW")
    assert load.needs == [("DW_LOAD_SLOT", 1)]


def test_xml_opens_file(fixture_path):
    from tws_parser.parser.xml_export import parse_xml
    schedules = parse_xml(str(fixture_path("08_xml_export_full.xml")))
    sales = next(s for s in schedules if s.name == "DAILY_SALES_LOAD")
    wait = next(j for j in sales.jobs if j.name == "WAIT_SALES_FEED")
    assert wait.opens == ["/data/feeds/sales_YYYYMMDD.csv"]


# -----------------------------------------------------------------------------
# Cross-format convergence (plan §10.5)
# -----------------------------------------------------------------------------

def test_text_and_xml_produce_same_ir_for_equivalent_input(fixture_path):
    """Fixture 02 (text) and fixture 07 (xml) describe the same schedule.
    They must produce identical ScheduleIR objects after normalisation."""
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.parser.xml_export import parse_xml

    text_ir = parse_composer_text(str(fixture_path("02_multi_job_with_follows.txt")))
    xml_ir = parse_xml(str(fixture_path("07_xml_export_single.xml")))

    assert len(text_ir) == len(xml_ir) == 1
    a, b = text_ir[0], xml_ir[0]
    assert a.name == b.name == "DAILY_SALES_LOAD"
    assert {j.name for j in a.jobs} == {j.name for j in b.jobs}
    # FOLLOWS chain identical
    a_follows = {(j.name, tuple(j.follows)) for j in a.jobs}
    b_follows = {(j.name, tuple(j.follows)) for j in b.jobs}
    assert a_follows == b_follows
