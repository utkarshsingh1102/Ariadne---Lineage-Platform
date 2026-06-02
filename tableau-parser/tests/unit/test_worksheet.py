"""
<worksheet> parsing (plan §6 step 5).
Each worksheet captures the fields it uses (datasource-dependencies) and
the shelf they're on (rows / cols / filter).
"""
import pytest


def test_worksheet_captured(fixture_path):
    from tableau_parser.parser.worksheet import parse_worksheets
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("01_simple_single_datasource.twb"))
    sheets = parse_worksheets(tree)
    assert len(sheets) == 1
    assert sheets[0].name == "Customers by Region"


def test_worksheet_field_dependencies(fixture_path):
    from tableau_parser.parser.worksheet import parse_worksheets
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("01_simple_single_datasource.twb"))
    sheets = parse_worksheets(tree)
    fields = {u.field_name for u in sheets[0].field_usages}
    assert "Region" in fields
    assert "CustomerName" in fields


def test_shelf_information_extracted(fixture_path):
    """rows / cols are extracted onto USES_FIELD properties."""
    from tableau_parser.parser.worksheet import parse_worksheets
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("01_simple_single_datasource.twb"))
    sheets = parse_worksheets(tree)
    shelves = {u.field_name: u.shelf for u in sheets[0].field_usages}
    assert shelves.get("Region") == "rows"
    assert shelves.get("CustomerName") == "cols"


def test_multiple_worksheets(fixture_path):
    from tableau_parser.parser.worksheet import parse_worksheets
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("05_dashboard_with_multiple_sheets.twb"))
    sheets = parse_worksheets(tree)
    names = {s.name for s in sheets}
    assert names == {"Monthly Sales", "Top Orders"}
