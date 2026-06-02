"""
FastAPI surface tests (plan §7).
GraphWriter mocked.
"""
import pytest


def test_health():
    from fastapi.testclient import TestClient
    from spark_parser.main import app
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


def test_version():
    from fastapi.testclient import TestClient
    from spark_parser.main import app
    client = TestClient(app)
    r = client.get("/version")
    assert r.status_code == 200
    assert "version" in r.json()


def test_parse_pyspark_endpoint(pyspark_fixture, graph_writer_mock, monkeypatch):
    from fastapi.testclient import TestClient
    from spark_parser.main import app
    monkeypatch.setattr("spark_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(pyspark_fixture("02_join_and_select.py")),
        "language_hint": "auto",
        "overwrite": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert "script_id" in body
    assert body["script_type"] == "pyspark"
    assert body["stats"]["source_tables"] >= 2
    assert body["stats"]["target_tables"] >= 1


def test_parse_sparksql_endpoint(sparksql_fixture, graph_writer_mock, monkeypatch):
    from fastapi.testclient import TestClient
    from spark_parser.main import app
    monkeypatch.setattr("spark_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(sparksql_fixture("03_merge_into.sql")),
        "language_hint": "auto",
    })
    assert r.status_code == 200
    assert r.json()["script_type"] == "sparksql"


def test_dynamic_table_name_surfaces_warning(pyspark_fixture, graph_writer_mock, monkeypatch):
    from fastapi.testclient import TestClient
    from spark_parser.main import app
    monkeypatch.setattr("spark_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(pyspark_fixture("07_dynamic_table_name.py")),
    })
    assert r.status_code == 200
    warnings = r.json().get("warnings", [])
    assert any(w.get("type") in {"dynamic_table_name", "lineage_partial"} for w in warnings)


def test_parse_project_endpoint_returns_modules_and_edges(
    graph_writer_mock, monkeypatch,
):
    """v0.2 §1 — /parse/project on a two-file fixture."""
    from pathlib import Path

    from fastapi.testclient import TestClient
    from spark_parser.main import app

    monkeypatch.setattr(
        "spark_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock,
    )
    fixtures = Path(__file__).resolve().parent.parent / "fixtures" / "projects" / "util_lib_pipeline"

    client = TestClient(app)
    r = client.post("/parse/project", json={
        "entry_path": str(fixtures / "entry.py"),
        "project_root": str(fixtures),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "entry_script_id" in body
    assert len(body["modules"]) == 2
    names = {Path(m["file_path"]).name for m in body["modules"]}
    assert names == {"entry.py", "util.py"}
    # At least one edge from entry → util resolved
    resolved = [e for e in body["import_edges"] if e["to_script_id"]]
    assert any(e["symbol"] == "enrich" for e in resolved)


def test_parse_project_rejects_missing_entry(graph_writer_mock, monkeypatch):
    from fastapi.testclient import TestClient
    from spark_parser.main import app

    client = TestClient(app)
    r = client.post("/parse/project", json={
        "entry_path": "/tmp/does_not_exist_xyz.py",
        "project_root": "/tmp",
    })
    assert r.status_code == 400


def test_parse_with_runtime_endpoint(graph_writer_mock, monkeypatch, pyspark_fixture):
    """v0.2 §11 — /parse/with-runtime correlates static IR + event log."""
    from pathlib import Path

    from fastapi.testclient import TestClient
    from spark_parser.main import app

    monkeypatch.setattr(
        "spark_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock,
    )
    event_log = Path(__file__).resolve().parent.parent / "fixtures" / "event_logs" / "orders_etl"
    client = TestClient(app)
    r = client.post("/parse/with-runtime", json={
        "file_path": str(pyspark_fixture("09_realistic_etl.py")),
        "event_log_path": str(event_log),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["runtime"]["sql_executions"] == 1
    assert body["runtime"]["jobs"] == 1
    assert body["runtime"]["stages"] == 2
    # At least one correlation between the static script and the runtime IR.
    assert len(body["correlations"]) >= 1


def test_oversized_file_rejected(tmp_path, monkeypatch):
    """Plan §8: MAX_FILE_SIZE_MB env limit."""
    from fastapi.testclient import TestClient
    from spark_parser.main import app
    big = tmp_path / "big.py"
    big.write_bytes(b"# big\n" + b"x" * (5 * 1024 * 1024))
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "1")
    client = TestClient(app)
    r = client.post("/parse", json={"file_path": str(big)})
    assert r.status_code in (400, 413)
