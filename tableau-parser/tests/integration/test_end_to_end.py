"""
End-to-end parse → IR (no Neo4j).
Smoke test: every fixture round-trips through parse_workbook() without raising.
"""
import pytest


FIXTURES = [
    "01_simple_single_datasource.twb",
    "02_calculated_fields.twb",
    "03_federated_join.twb",
    "04_custom_sql.twb",
    "05_dashboard_with_multiple_sheets.twb",
    "06_packaged_workbook.twbx",
    "07_parameters.twb",
    "08_realistic_dashboard.twb",
    "09_full_reference.twb",
]


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_parses_to_workbook_ir(parse, fixture_path, name):
    from tableau_parser.parser.workbook import parse_workbook
    ir = parse_workbook(str(fixture_path(name)))
    assert ir is not None
    assert ir.id and len(ir.id) == 16
    assert ir.name


def test_realistic_dashboard_stats(fixture_path):
    """Plan §7: stats from example API response."""
    from tableau_parser.parser.workbook import parse_workbook
    ir = parse_workbook(str(fixture_path("08_realistic_dashboard.twb")))
    assert len(ir.datasources) == 4
    assert len(ir.parameters) == 2
    assert len(ir.dashboards) == 2
    assert len(ir.worksheets) == 3
    # 4 datasources × 1+ table each → at least 4 physical tables
    all_tables = [t for ds in ir.datasources for t in ds.tables]
    assert len(all_tables) >= 4


@pytest.mark.slow
def test_realistic_under_30s(fixture_path):
    """Plan §15: 50MB / ~20 datasources in <30s. We just upper-bound here."""
    import time
    from tableau_parser.parser.workbook import parse_workbook
    start = time.time()
    parse_workbook(str(fixture_path("08_realistic_dashboard.twb")))
    assert time.time() - start < 30
