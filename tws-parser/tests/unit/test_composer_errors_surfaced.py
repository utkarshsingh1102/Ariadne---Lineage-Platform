"""Phase 1 — the silent-zero fix.

Confirms that lexer/parser diagnostics no longer disappear into
``CollectingErrorListener`` — they reach the API response as ``Warning``
entries and ``status`` reflects what happened.
"""
from __future__ import annotations

from pathlib import Path


def test_parse_composer_text_with_errors_returns_tuple(fixture_path):
    """The new tuple-returning function exposes the error list."""
    from tws_parser.parser.composer import parse_composer_text_with_errors

    schedules, errors = parse_composer_text_with_errors(
        str(fixture_path("01_single_schedule_single_job.txt"))
    )
    assert isinstance(schedules, list)
    assert isinstance(errors, list)
    # A well-formed fixture parses cleanly.
    assert errors == []
    assert len(schedules) >= 1


def test_legacy_parse_composer_text_still_returns_list_only(fixture_path):
    """The convenience wrapper preserves the old shape for ~20 existing callers."""
    from tws_parser.parser.composer import parse_composer_text

    schedules = parse_composer_text(
        str(fixture_path("01_single_schedule_single_job.txt"))
    )
    assert isinstance(schedules, list)
    assert len(schedules) >= 1


def test_malformed_fixture_surfaces_parse_errors(fixture_path, monkeypatch, graph_writer_mock, rdbms_writer_mock):
    """The API path must NOT return status=ok on a malformed file.

    Uses the deliberately-broken ``10_malformed.txt`` so the test stays
    meaningful as Phase 2 (grammar extensions) reduces the surface of
    parse failures. Phase 1's loud-failure invariant is permanent — any
    parse error must reach the warnings list with line/column.
    """
    from fastapi.testclient import TestClient
    from tws_parser.main import app

    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(fixture_path("10_malformed.txt")),
        "write_neo4j": False,
        "write_postgres": False,
    })
    assert r.status_code == 200, r.text
    body = r.json()

    # Standing requirement: never ok with errors.
    assert body["status"] in {"partial", "failed"}, (
        f"status must reflect parse errors; got {body['status']!r}"
    )
    assert body["warnings"], "warnings list must NOT be empty on a broken parse"

    parse_error_warnings = [w for w in body["warnings"] if w["type"] == "parse_error"]
    assert parse_error_warnings, "expected ≥1 parse_error warning"

    # Each parse_error warning carries line + column.
    for w in parse_error_warnings:
        assert "line" in w and w["line"] is not None
        assert "column" in w and w["column"] is not None
        assert w["detail"], "parse_error warnings must include the ANTLR message"


def test_stress_fixture_parses_cleanly_post_phase2(monkeypatch, graph_writer_mock, rdbms_writer_mock):
    """After Phase 2 grammar extensions, the stress fixture parses with status=ok.

    Cross-file unresolved deps are expected as warnings of type
    ``unresolved_dependency`` (Phase 4 will refine that), but no
    ``parse_error`` warnings should remain.
    """
    from fastapi.testclient import TestClient
    from tws_parser.main import app

    monkeypatch.setattr("tws_parser.graph.writer.GraphWriter", lambda *a, **k: graph_writer_mock)
    monkeypatch.setattr("tws_parser.rdbms.writer.RDBMSWriter", lambda *a, **k: rdbms_writer_mock)

    stress_path = (
        Path(__file__).resolve().parents[3]
        / "tableau-improvement/TWS-improvement/tws_lineage_stress_test.txt"
    )
    if not stress_path.exists():
        import pytest
        pytest.skip(f"Stress fixture not found at {stress_path}")

    client = TestClient(app)
    r = client.post("/parse", json={
        "file_path": str(stress_path),
        "write_neo4j": False,
        "write_postgres": False,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok", f"status was {body['status']!r}; parse errors leaked"
    assert body["parsed_schedules"] == 5
    # Phase 3+ will give exact-job counts; for now assert ≥16 (plan expected 16,
    # actual is 17 — CLEANUP_LANDING + LOAD_ROLLBACK + DR_REPLICATE etc).
    assert body["parsed_jobs"] >= 16
    parse_errors = [w for w in body["warnings"] if w["type"] == "parse_error"]
    assert parse_errors == [], f"unexpected parse errors: {parse_errors}"
