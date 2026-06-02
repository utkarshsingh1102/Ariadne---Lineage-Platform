"""Source-line stamping + coverage harness (target plan Step 1).

Asserts:
- Every IR class that grew a `line` field actually gets it populated.
- The fixture-01 parse produces zero unmapped-element warnings (the
  "nothing missed" invariant — see plan §A.6).
"""
from __future__ import annotations


def test_workbook_line_range_populated(fixture_path):
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("01_simple_single_datasource.twb"))
    assert wb.line is not None and wb.line >= 1
    assert wb.line_end is not None and wb.line_end >= wb.line


def test_datasource_line_populated(fixture_path):
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("01_simple_single_datasource.twb"))
    assert wb.datasources, "fixture has at least one datasource"
    assert wb.datasources[0].line is not None


def test_connection_line_populated(fixture_path):
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("01_simple_single_datasource.twb"))
    conns = [c for d in wb.datasources for c in d.connections]
    assert conns, "fixture has at least one connection"
    assert all(c.line is not None for c in conns)


def test_table_line_populated(fixture_path):
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("01_simple_single_datasource.twb"))
    tables = [t for d in wb.datasources for t in d.tables]
    assert tables, "fixture has at least one table"
    assert all(t.line is not None for t in tables)


def test_reads_table_line_propagated_from_table(fixture_path):
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("01_simple_single_datasource.twb"))
    for d in wb.datasources:
        for r in d.reads_tables:
            assert r.line is not None, (
                "READS_TABLE edge must carry the table's source line"
            )


def test_field_line_populated_for_physical_and_calculated(fixture_path):
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("02_calculated_fields.twb"))
    fields = [f for d in wb.datasources for f in d.fields]
    assert fields, "fixture has at least one field"
    assert all(f.line is not None for f in fields), (
        "every <column> has a sourceline; FieldIR.line must be stamped"
    )


def test_derives_from_line_inherits_from_calc_field(fixture_path):
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("02_calculated_fields.twb"))
    for d in wb.datasources:
        if not d.derives_from:
            continue
        assert all(e.line is not None for e in d.derives_from), (
            "DERIVES_FROM_IR.line must inherit from the calc field"
        )


def test_worksheet_and_dashboard_lines(fixture_path):
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("05_dashboard_with_multiple_sheets.twb"))
    assert wb.worksheets and all(w.line is not None for w in wb.worksheets)
    assert wb.dashboards and all(d.line is not None for d in wb.dashboards)


def test_parameter_line_populated(fixture_path):
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("07_parameters.twb"))
    assert wb.parameters and all(p.line is not None for p in wb.parameters)


def test_coverage_zero_warnings_on_fixture_01(fixture_path):
    """Baseline coverage assertion: fixture 01 is the simplest workbook in
    the suite. With every well-known tag mapped or ignored, the resulting
    WorkbookIR.warnings list must contain no `unmapped_element` entries.

    When you add support for a new XML element family (e.g. groups, sets,
    actions), add its tag to ``parser/coverage.py::_MAPPED_TAGS``. Anything
    you intentionally skip goes to ``_IGNORE_TAGS``.
    """
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("01_simple_single_datasource.twb"))
    unmapped = [w for w in wb.warnings if w.get("type") == "unmapped_element"]
    assert unmapped == [], (
        "fixture 01 has unmapped tags: "
        + ", ".join(sorted({w.get("detail") for w in unmapped}))
    )
