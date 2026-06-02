"""Phase 1 — strict mode.

When ``strict=true``, any collected lexer/parser error must produce HTTP
422 rather than partial IR. Unresolved cross-file dependencies are NOT
parse errors and never trip strict.
"""
from __future__ import annotations

from pathlib import Path


def test_strict_mode_raises_422_on_parse_errors(fixture_path, monkeypatch, graph_writer_mock, rdbms_writer_mock):
    from fastapi.testclient import TestClient
    from tws_parser.main import app

    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(fixture_path("10_malformed.txt")),
        "strict": True,
        "write_neo4j": False,
        "write_postgres": False,
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["message"] == "Strict mode: collected parse errors"
    assert detail["parse_errors"], "422 body must include the parse-error list"
    # Each surfaced error carries line/column/detail.
    for e in detail["parse_errors"]:
        assert e["line"] is not None
        assert e["column"] is not None
        assert e["detail"]


def test_strict_mode_passes_on_clean_fixture(fixture_path, monkeypatch, graph_writer_mock, rdbms_writer_mock):
    """A well-formed fixture must NOT raise 422 even when strict=true."""
    from fastapi.testclient import TestClient
    from tws_parser.main import app

    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(fixture_path("02_multi_job_with_follows.txt")),
        "strict": True,
        "write_neo4j": True,
        "write_postgres": True,
        "overwrite": True,
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
