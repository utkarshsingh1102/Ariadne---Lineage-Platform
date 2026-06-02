"""
ANTLR Visitor → ScheduleIR (plan §10.2).
Plan §10.6: 100% coverage required on visitor/ir_visitor.py.
"""
import pytest


def _visit_text(text: str):
    from antlr4 import InputStream, CommonTokenStream
    from tws_parser.generated.TWSComposerLexer import TWSComposerLexer
    from tws_parser.generated.TWSComposerParser import TWSComposerParser
    from tws_parser.visitor.ir_visitor import TWSIRVisitor

    stream = InputStream(text)
    lexer = TWSComposerLexer(stream)
    tokens = CommonTokenStream(lexer)
    parser = TWSComposerParser(tokens)
    tree = parser.compilationUnit()
    return TWSIRVisitor().visit(tree)


def test_visitor_returns_schedule_list(fixture_text):
    schedules = _visit_text(fixture_text("01_single_schedule_single_job.txt"))
    assert isinstance(schedules, list)
    assert len(schedules) == 1
    s = schedules[0]
    assert s.workstation == "WORKSTATION_A"
    assert s.scheduler == "MASTER"
    assert s.name == "SIMPLE_LOAD"


def test_schedule_header_fields(fixture_text):
    schedules = _visit_text(fixture_text("02_multi_job_with_follows.txt"))
    s = schedules[0]
    assert s.start_time == "05:30"
    assert s.end_time == "09:00"
    assert s.carry_forward is True
    assert s.priority == 50
    assert s.run_cycle == "EVERY_WEEKDAY"


def test_jobs_in_order(fixture_text):
    schedules = _visit_text(fixture_text("02_multi_job_with_follows.txt"))
    s = schedules[0]
    names = [j.name for j in s.jobs]
    assert names == ["EXTRACT_ORDERS", "TRANSFORM_ORDERS", "LOAD_ORDERS_TO_DW"]


def test_job_properties(fixture_text):
    schedules = _visit_text(fixture_text("02_multi_job_with_follows.txt"))
    s = schedules[0]
    extract = next(j for j in s.jobs if j.name == "EXTRACT_ORDERS")
    assert extract.script_path.endswith("run.sh") or extract.script_path.endswith("extract_orders.mp")
    assert extract.stream_logon == "tws_user"
    assert extract.recovery == "STOP"
    assert "Extract orders" in (extract.description or "")


def test_job_follows_dependency_captured(fixture_text):
    schedules = _visit_text(fixture_text("02_multi_job_with_follows.txt"))
    s = schedules[0]
    transform = next(j for j in s.jobs if j.name == "TRANSFORM_ORDERS")
    assert "EXTRACT_ORDERS" in transform.follows


def test_schedule_level_follows(fixture_text):
    schedules = _visit_text(fixture_text("03_schedule_level_dependency.txt"))
    sales = next(s for s in schedules if s.name == "DAILY_SALES_LOAD")
    # The .@ wildcard means depend-on-schedule
    assert len(sales.schedule_follows) == 1
    dep = sales.schedule_follows[0]
    assert dep.target_schedule == "NIGHTLY_INFRA_CHECK"
    assert dep.target_workstation == "WORKSTATION_A"
    assert dep.wildcard is True


def test_needs_resource_extracted(fixture_text):
    schedules = _visit_text(fixture_text("04_resource_and_file_deps.txt"))
    s = schedules[0]
    process = next(j for j in s.jobs if j.name == "PROCESS_FEED")
    assert process.needs == [("FEED_PROCESSOR_SLOT", 1)]


def test_opens_file_extracted(fixture_text):
    schedules = _visit_text(fixture_text("04_resource_and_file_deps.txt"))
    s = schedules[0]
    wait = next(j for j in s.jobs if j.name == "WAIT_FOR_FEED")
    assert wait.opens == ["/data/feeds/sales_YYYYMMDD.csv"]


def test_realistic_fixture_three_schedules(fixture_text):
    schedules = _visit_text(fixture_text("06_realistic_dump_many_schedules.txt"))
    assert len(schedules) == 3
    names = {s.name for s in schedules}
    assert names == {"NIGHTLY_INFRA_CHECK", "DAILY_SALES_LOAD", "FINANCE_RECON"}
