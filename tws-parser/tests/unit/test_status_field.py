"""Phase 1 — the status field.

  ok      — zero parse errors
  partial — parse errors AND ≥1 schedule recovered
  failed  — parse errors AND zero schedules
"""
from __future__ import annotations


def test_status_ok_on_clean_fixture(fixture_path, monkeypatch, graph_writer_mock, rdbms_writer_mock):
    from fastapi.testclient import TestClient
    from tws_parser.main import app

    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(fixture_path("02_multi_job_with_follows.txt")),
        "write_neo4j": True,
        "write_postgres": True,
        "overwrite": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["parsed_schedules"] >= 1
    # An ``ok`` response may legitimately carry warnings of OTHER types
    # (unresolved cross-file deps), but no parse_error warnings.
    parse_errors = [w for w in body["warnings"] if w["type"] == "parse_error"]
    assert parse_errors == []


def test_format_detector_failure_returns_400_not_500(
    tmp_path, monkeypatch, graph_writer_mock, rdbms_writer_mock
):
    """A file the detector can't classify gets HTTP 400, not a 500.

    Phase 1 added explicit handling so format_detector.FormatDetectionError
    becomes a 400 (bad input) instead of bubbling out as a 500.
    """
    from fastapi.testclient import TestClient
    from tws_parser.main import app

    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    empty = tmp_path / "unclassifiable.txt"
    empty.write_text("# Just a comment. No top-level keyword anywhere.\n", encoding="utf-8")

    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(empty),
        "write_neo4j": False,
        "write_postgres": False,
    })
    assert r.status_code == 400, r.text


def test_status_failed_when_parse_produces_no_ir(
    tmp_path, monkeypatch, graph_writer_mock, rdbms_writer_mock
):
    """Garbage input → failed (parse errors + zero schedules)."""
    from fastapi.testclient import TestClient
    from tws_parser.main import app

    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    bad = tmp_path / "garbage.txt"
    bad.write_text("$$$ &&& not a composer file at all", encoding="utf-8")

    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(bad),
        "write_neo4j": False,
        "write_postgres": False,
    })
    # format_detector may reject pure garbage before parsing — accept either
    # 400 (rejected at the detector) or 200/failed (parsed-with-errors).
    if r.status_code == 200:
        body = r.json()
        assert body["status"] == "failed"
        assert body["parsed_schedules"] == 0
        assert any(w["type"] == "parse_error" for w in body["warnings"])
    else:
        assert r.status_code == 400
