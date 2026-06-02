"""Phase 3 — QMC tasks XML parser."""
from __future__ import annotations

from pathlib import Path

import pytest

from qlikview_parser.ids import sha256_id
from qlikview_parser.server_meta import (
    ServerMetaResult,
    ServerTask,
    ServerTrigger,
    parse_directory,
    parse_tasks_xml,
)


def test_missing_file_yields_error_diagnostic(tmp_path):
    res = parse_tasks_xml(tmp_path / "missing.xml")
    codes = [d.code for d in res.diagnostics]
    assert "QV-SERVER-NOT-FOUND" in codes
    assert res.tasks == []


def test_malformed_xml_yields_parse_diagnostic(tmp_path):
    p = tmp_path / "broken.xml"
    p.write_text("<<<not xml>>>")
    res = parse_tasks_xml(p)
    codes = [d.code for d in res.diagnostics]
    assert "QV-SERVER-PARSE" in codes


def test_single_task_with_scheduled_trigger(tmp_path):
    p = tmp_path / "tasks.xml"
    p.write_text(
        "<Tasks>"
        "  <Task>"
        "    <Id>guid-1</Id>"
        "    <Name>Reload Sales</Name>"
        "    <Type>Reload</Type>"
        "    <AppPath>C:\\apps\\sales.qvw</AppPath>"
        "    <Enabled>true</Enabled>"
        "    <Trigger>"
        "      <Id>trg-1</Id>"
        "      <Type>Scheduled</Type>"
        "      <Schedule>0 6 * * *</Schedule>"
        "    </Trigger>"
        "  </Task>"
        "</Tasks>"
    )
    res = parse_tasks_xml(p)
    assert len(res.tasks) == 1
    t = res.tasks[0]
    assert t.task_id == "guid-1"
    assert t.name == "Reload Sales"
    assert t.task_type == "reload"
    assert t.app_path and "sales.qvw" in t.app_path
    assert t.enabled is True

    assert len(res.triggers) == 1
    trig = res.triggers[0]
    assert trig.kind == "scheduled"
    assert trig.schedule == "0 6 * * *"


def test_edx_trigger_emits_cross_task_triggers_edge(tmp_path):
    """An EDX trigger on Task A pointing at Task B's name produces a
    TRIGGERS lineage edge from the EDX trigger to Task B."""
    p = tmp_path / "edx.xml"
    p.write_text(
        "<Tasks>"
        "  <Task>"
        "    <Id>a</Id><Name>Stage</Name><Type>Reload</Type>"
        "    <AppPath>stage.qvw</AppPath>"
        "    <Trigger>"
        "      <Id>edx-1</Id>"
        "      <Type>EDX</Type>"
        "      <Target>Mart</Target>"
        "    </Trigger>"
        "  </Task>"
        "  <Task>"
        "    <Id>b</Id><Name>Mart</Name><Type>Reload</Type>"
        "    <AppPath>mart.qvw</AppPath>"
        "  </Task>"
        "</Tasks>"
    )
    res = parse_tasks_xml(p)
    triggers_edges = [e for e in res.edges if e.rel == "TRIGGERS"]
    assert len(triggers_edges) == 1
    e = triggers_edges[0]
    assert e.dst_id == sha256_id("task::b")
    assert e.transform == "EDX"


def test_disabled_task_records_enabled_false(tmp_path):
    p = tmp_path / "tasks.xml"
    p.write_text(
        "<Tasks><Task><Id>x</Id><Name>Off</Name>"
        "<Type>Reload</Type><Enabled>false</Enabled>"
        "<AppPath>app.qvw</AppPath></Task></Tasks>"
    )
    res = parse_tasks_xml(p)
    assert res.tasks[0].enabled is False


def test_parse_directory_merges_multiple_xml_files(tmp_path):
    d = tmp_path / "qmc"
    d.mkdir()
    (d / "a.xml").write_text(
        "<Tasks><Task><Id>a</Id><Name>A</Name><Type>Reload</Type>"
        "<AppPath>a.qvw</AppPath></Task></Tasks>"
    )
    (d / "b.xml").write_text(
        "<Tasks><Task><Id>b</Id><Name>B</Name><Type>Reload</Type>"
        "<AppPath>b.qvw</AppPath></Task></Tasks>"
    )
    res = parse_directory(d)
    assert {t.task_id for t in res.tasks} == {"a", "b"}


def test_bare_single_task_root_is_accepted(tmp_path):
    """A standalone ``<Task>`` root (no ``<Tasks>`` wrapper) should still
    parse — QMC's per-task export emits exactly that shape."""
    p = tmp_path / "single.xml"
    p.write_text(
        "<Task><Id>solo</Id><Name>Solo</Name><Type>Reload</Type>"
        "<AppPath>solo.qvw</AppPath></Task>"
    )
    res = parse_tasks_xml(p)
    assert len(res.tasks) == 1
    assert res.tasks[0].task_id == "solo"
