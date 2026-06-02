"""Step 5 — dashboard zones + actions."""
from __future__ import annotations

from io import BytesIO

from lxml import etree

from tableau_parser.parser.dashboard import parse_dashboards


def _tree(xml: str):
    return etree.parse(BytesIO(xml.encode("utf-8")))


def test_non_worksheet_zone_becomes_dashboard_zone():
    tree = _tree("""
    <workbook>
      <dashboards>
        <dashboard name="D1">
          <zones>
            <zone type="worksheet" name="Sales by Region" />
            <zone type="filter" name="Region Filter" />
            <zone type="parameter" name="Param Control" param="[Year]" />
            <zone type="text" name="Title" />
          </zones>
        </dashboard>
      </dashboards>
    </workbook>
    """)
    d = parse_dashboards(tree)[0]
    # Worksheet still surfaces via displayed_worksheets
    assert d.displayed_worksheets == ["Sales by Region"]
    kinds = sorted(z.kind for z in d.zones)
    assert kinds == ["filter", "parameter", "text"]
    # Parameter zone captured the target parameter name
    param_zone = next(z for z in d.zones if z.kind == "parameter")
    assert param_zone.target_parameter == "Year"


def test_filter_action_captures_source_target_and_fields():
    tree = _tree("""
    <workbook>
      <dashboards>
        <dashboard name="D1">
          <zones>
            <zone type="worksheet" name="WS1" />
            <zone type="worksheet" name="WS2" />
          </zones>
          <actions>
            <action class="filter" name="Highlight Region"
                    source-sheets="[WS1]"
                    target-sheets="[WS2]"
                    fields="[Region]" />
          </actions>
        </dashboard>
      </dashboards>
    </workbook>
    """)
    d = parse_dashboards(tree)[0]
    assert len(d.actions) == 1
    a = d.actions[0]
    assert a.kind == "filter"
    assert a.source_sheets == ["WS1"]
    assert a.target_sheets == ["WS2"]
    assert a.fields == ["Region"]


def test_parameter_action_captures_target_parameter():
    tree = _tree("""
    <workbook>
      <dashboards>
        <dashboard name="D1">
          <zones>
            <zone type="worksheet" name="WS1" />
          </zones>
          <actions>
            <action class="set-parameter" name="Sync Year"
                    source-sheets="[WS1]"
                    parameter="[Year]" />
          </actions>
        </dashboard>
      </dashboards>
    </workbook>
    """)
    a = parse_dashboards(tree)[0].actions[0]
    assert a.kind == "parameter"
    assert a.parameter_name == "Year"
    assert a.source_sheets == ["WS1"]


def test_zone_and_action_have_source_lines():
    tree = _tree("""
    <workbook>
      <dashboards>
        <dashboard name="D1">
          <zones>
            <zone type="filter" name="F" />
          </zones>
          <actions>
            <action class="filter" name="A" source-sheets="[X]" target-sheets="[Y]" />
          </actions>
        </dashboard>
      </dashboards>
    </workbook>
    """)
    d = parse_dashboards(tree)[0]
    assert d.zones[0].line is not None
    assert d.actions[0].line is not None
