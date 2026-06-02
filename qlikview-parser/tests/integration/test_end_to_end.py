"""
End-to-end parse tests — every fixture must parse without raising.
This is the bare-minimum smoke test the developer can run after every change.
"""
import pytest


FIXTURES = [
    "01_simple_sql_load.qvs",
    "02_resident_load.qvs",
    "03_left_join.qvs",
    "04_concatenate.qvs",
    "05_file_load.qvs",
    "06_variables_and_includes.qvs",
    "07_subroutines.qvs",
    "08_realistic_dashboard.qvs",
    "09_comments_and_edge_cases.qvs",
    "10_qvd_load.qvs",
]


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_parses_without_exception(parse, name):
    app = parse(name)
    assert app is not None
    assert app.app_name == name.replace(".qvs", "")


def test_json_export_round_trip(parser_no_neo4j, fixture_path, tmp_path):
    """Plan §A: JSON export shape must be stable."""
    parser_no_neo4j.parse_qvs_file(str(fixture_path("01_simple_sql_load.qvs")))
    out = tmp_path / "out.json"
    parser_no_neo4j.export_to_json(str(out))
    assert out.exists() and out.stat().st_size > 0

    import json
    data = json.loads(out.read_text())
    assert "apps" in data
    assert "export_date" in data
    assert data["total_apps"] >= 1


@pytest.mark.slow
def test_realistic_dashboard_under_20s(parser_no_neo4j, fixture_path):
    """Plan §15: 5000-line scripts must parse in under 20s."""
    import time
    start = time.time()
    parser_no_neo4j.parse_qvs_file(str(fixture_path("08_realistic_dashboard.qvs")))
    elapsed = time.time() - start
    assert elapsed < 20, f"Realistic fixture took {elapsed:.1f}s (>20s)"


# -----------------------------------------------------------------------------
# Aggregate stats from the plan's example API response (§7)
# -----------------------------------------------------------------------------

def test_realistic_dashboard_stats(parse):
    app = parse("08_realistic_dashboard.qvs")
    # Per plan §7 example response shape — adapt thresholds as the parser matures
    assert len(app.loads) >= 6           # Customer, Orders, OrdersByCustomer, Product, Calendar, OrdersCopy
    assert len(app.connections) >= 3     # ODBC + OLEDB + LIB
    assert len(app.joins) >= 2           # explicit + implicit
    assert any(f.is_synthetic for f in app.fields)
