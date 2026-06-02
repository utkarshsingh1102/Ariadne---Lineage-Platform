"""
FastAPI surface tests (plan §7).
Uses TestClient with the GraphWriter mocked.
"""
import pytest


def test_health(graph_writer_mock):
    from fastapi.testclient import TestClient
    from tableau_parser.main import app
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


def test_version():
    from fastapi.testclient import TestClient
    from tableau_parser.main import app
    client = TestClient(app)
    r = client.get("/version")
    assert r.status_code == 200
    assert "version" in r.json()


def test_parse_endpoint_validates_file_path(graph_writer_mock):
    from fastapi.testclient import TestClient
    from tableau_parser.main import app
    client = TestClient(app)
    r = client.post("/parse", json={"file_path": "/does/not/exist.twb"})
    assert r.status_code in (400, 404, 422)


def test_parse_returns_stats(fixture_path, graph_writer_mock, monkeypatch):
    from fastapi.testclient import TestClient
    from tableau_parser.main import app
    # Wire the mock into the app's writer dependency.
    monkeypatch.setattr("tableau_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(fixture_path("01_simple_single_datasource.twb")),
        "overwrite": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert "workbook_id" in body
    assert body["stats"]["datasources"] == 1


def test_oversized_file_rejected(tmp_path, monkeypatch):
    """Plan §8: MAX_FILE_SIZE_MB env limit."""
    from fastapi.testclient import TestClient
    from tableau_parser.main import app
    big = tmp_path / "big.twb"
    big.write_bytes(b"<workbook/>" + b"\x00" * (10 * 1024 * 1024))
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "1")
    client = TestClient(app)
    r = client.post("/parse", json={"file_path": str(big)})
    assert r.status_code in (400, 413)
