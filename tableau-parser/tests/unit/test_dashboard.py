"""
<dashboard> parsing (plan §6 step 6).
Walk <zones> recursively; emit DisplaysWorksheetIR per referenced worksheet,
deduplicated.
"""
import pytest


def test_dashboard_captured(fixture_path):
    from tableau_parser.parser.dashboard import parse_dashboards
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("05_dashboard_with_multiple_sheets.twb"))
    dashes = parse_dashboards(tree)
    assert len(dashes) == 1
    assert dashes[0].name == "Sales Overview"


def test_displays_worksheet_deduplicated(fixture_path):
    """Fixture 05 references 'Monthly Sales' twice — must produce only one edge."""
    from tableau_parser.parser.dashboard import parse_dashboards
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("05_dashboard_with_multiple_sheets.twb"))
    dashes = parse_dashboards(tree)
    targets = [w for w in dashes[0].displayed_worksheets]
    assert targets.count("Monthly Sales") == 1, \
        f"Expected dedup; got duplicates: {targets}"
    assert "Top Orders" in targets


def test_shared_worksheets_across_dashboards(fixture_path):
    """Fixture 08 has two dashboards that share 'Top Customers'."""
    from tableau_parser.parser.dashboard import parse_dashboards
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("08_realistic_dashboard.twb"))
    dashes = parse_dashboards(tree)
    by_name = {d.name: d.displayed_worksheets for d in dashes}
    assert "Top Customers" in by_name["Executive Overview"]
    assert "Top Customers" in by_name["Customer Detail"]


def test_non_worksheet_zones_ignored(tmp_path):
    """Plan §9.1 + §14: web-page zones, image zones, blank containers are
    not worksheets — must be skipped silently."""
    from tableau_parser.parser.dashboard import parse_dashboards
    from tableau_parser.extractor.xml_loader import load_twb

    snip = tmp_path / "mixed_zones.twb"
    snip.write_text("""<?xml version='1.0'?>
<workbook version='2024.1'><datasources/>
  <worksheets><worksheet name='Real'><table><view/></table></worksheet></worksheets>
  <dashboards><dashboard name='D'><zones>
    <zone name='Real' type='worksheet'/>
    <zone type='web-page' url='https://example.com'/>
    <zone type='image' filename='logo.png'/>
    <zone type='blank'/>
  </zones></dashboard></dashboards></workbook>""")

    tree = load_twb(snip)
    dashes = parse_dashboards(tree)
    assert dashes[0].displayed_worksheets == ["Real"]
