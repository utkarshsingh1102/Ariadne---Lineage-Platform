"""
FastAPI surface tests (plan §8).
GraphWriter and RDBMSWriter both mocked.
"""
import pytest


def test_health(graph_writer_mock, rdbms_writer_mock):
    from fastapi.testclient import TestClient
    from tws_parser.main import app
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # Plan §13: health must verify BOTH stores
    assert "neo4j" in body
    assert "postgres" in body


def test_version():
    from fastapi.testclient import TestClient
    from tws_parser.main import app
    client = TestClient(app)
    r = client.get("/version")
    assert r.status_code == 200


def test_parse_returns_stats(fixture_path, monkeypatch, graph_writer_mock, rdbms_writer_mock):
    from fastapi.testclient import TestClient
    from tws_parser.main import app
    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    client = TestClient(app)
    r = client.post("/parse", json={
        "input_path": str(fixture_path("02_multi_job_with_follows.txt")),
        "format": "auto",
        "write_neo4j": True,
        "write_postgres": True,
        "overwrite": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["parsed_schedules"] == 1
    assert body["parsed_jobs"] == 3
    assert body["stats"]["follows_dependencies"] >= 2


def test_parse_multi_returns_commonality(
    fixture_path, monkeypatch, graph_writer_mock, rdbms_writer_mock,
):
    """POST /parse/multi with the two overlap fixtures returns 200 with
    a populated commonality report (shared workstation + calendar, the
    cross-file FOLLOWS edge)."""
    from fastapi.testclient import TestClient
    from tws_parser.main import app
    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter",
                        lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter",
                        lambda *a, **k: rdbms_writer_mock)

    a = str(fixture_path("multi/a_ingestion.txt"))
    b = str(fixture_path("multi/b_reporting.txt"))

    client = TestClient(app)
    r = client.post("/parse/multi", json={
        "file_paths": [a, b],
        "write_neo4j": True,
        "write_postgres": False,
        "overwrite": False,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"

    shared = body["commonality"]["shared_entities"]
    shared_ws_names = {e["name"] for e in shared.get("Workstation", [])}
    shared_cal_names = {e["name"] for e in shared.get("Calendar", [])}
    assert "ETL_AGENT_01" in shared_ws_names
    assert "WORKDAYS" in shared_cal_names

    cross = body["commonality"]["cross_file_follows"]
    assert any(
        "REFRESH_REPORTS" in cf["from_job_qualified"]
        and "LOAD_FACTS" in cf["to_job_qualified"]
        for cf in cross
    ), "Expected cross-file FOLLOWS REFRESH_REPORTS → LOAD_FACTS"

    assert body["merged_stats"]["workstations"] == 2     # ETL + BI
    assert body["merged_stats"]["calendars"] == 1
    assert body["merged_stats"]["files"] == 2


def test_parse_multi_passes_source_files_to_writer(
    fixture_path, monkeypatch, graph_writer_mock, rdbms_writer_mock,
):
    """The route must forward the provenance dict to write_topology so the
    writer can populate the source_files property on every node."""
    from fastapi.testclient import TestClient
    from tws_parser.main import app
    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter",
                        lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter",
                        lambda *a, **k: rdbms_writer_mock)
    captured: dict[str, object] = {}

    def _capture_write(unit, deps, overwrite=False, source_files=None):
        captured["source_files"] = source_files
        return {"nodes_written": 0}

    graph_writer_mock.write_topology = _capture_write

    a = str(fixture_path("multi/a_ingestion.txt"))
    b = str(fixture_path("multi/b_reporting.txt"))
    client = TestClient(app)
    r = client.post("/parse/multi", json={"file_paths": [a, b]})
    assert r.status_code == 200

    sf = captured["source_files"]
    assert sf is not None and isinstance(sf, dict) and len(sf) > 0
    # ETL_AGENT_01 should appear in BOTH files' provenance.
    shared_ws_ids = [nid for nid, files in sf.items() if set(files) == {a, b}]
    assert len(shared_ws_ids) >= 2     # at least the workstation + calendar


def test_parse_multi_strict_mode_fails_on_any_file_error(
    fixture_path, tmp_path, monkeypatch, graph_writer_mock, rdbms_writer_mock,
):
    """strict=true must return 422 if ANY file has parse errors."""
    from fastapi.testclient import TestClient
    from tws_parser.main import app
    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter",
                        lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter",
                        lambda *a, **k: rdbms_writer_mock)

    bad = tmp_path / "malformed.txt"
    bad.write_text("SCHEDULE WS#STREAM\n  AT 0500\n: $BROKEN$\nEND\n")
    good = str(fixture_path("multi/a_ingestion.txt"))

    client = TestClient(app)
    r = client.post("/parse/multi", json={
        "file_paths": [good, str(bad)],
        "strict": True,
        "write_neo4j": False,
    })
    assert r.status_code == 422


def test_excel_export_endpoint(fixture_path, monkeypatch, graph_writer_mock, rdbms_writer_mock):
    """Plan §8: /export/excel returns a valid .xlsx for a given filter."""
    from fastapi.testclient import TestClient
    from tws_parser.main import app
    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    client = TestClient(app)
    r = client.post("/export/excel", json={
        "filter": {"start_time_min": "05:30", "start_time_max": "06:30"},
    })
    assert r.status_code == 200
    assert r.headers["content-type"].endswith(
        "spreadsheetml.sheet"
    ) or r.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    )
    # First 4 bytes of any .xlsx are the ZIP magic 'PK\x03\x04'
    assert r.content[:4] == b"PK\x03\x04"
