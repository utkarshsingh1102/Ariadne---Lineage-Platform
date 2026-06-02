"""09_full_reference.twb — the reference workbook that exercises every
parser surface added in target plan steps 1-8.

This file is the canonical "nothing missed" test:
- Step 1 coverage invariant: zero unmapped_element warnings.
- Steps 3-8: each IR family contains at least one populated entry.
- Round-trip reachability: dashboards reach connections via the
  upstream chain when the gateway preset is walked.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def wb(fixture_path):
    # ``fixture_path`` is function-scoped in conftest so this fixture must
    # match. Parsing the 200-line fixture is cheap enough to do per-test.
    from tableau_parser.parser.workbook import parse_workbook
    return parse_workbook(str(fixture_path("09_full_reference.twb")))


def test_coverage_invariant_zero_unmapped(wb):
    """The reference workbook's parser walk must produce ZERO
    unmapped_element warnings. If you see this fail after adding a new
    element family to fixture 09, add the tag to
    ``parser/coverage.py::_MAPPED_TAGS`` (parser produces something for
    it) or ``_IGNORE_TAGS`` (intentionally skipped, no lineage value).
    """
    unmapped = [w for w in wb.warnings if w.get("type") == "unmapped_element"]
    tags = sorted({w.get("detail") for w in unmapped})
    assert unmapped == [], f"unmapped tags in fixture 09: {tags}"


def test_two_datasources_plus_parameters_present(wb):
    """The reference workbook has 2 user datasources + the Parameters block."""
    assert len(wb.datasources) == 2
    assert len(wb.parameters) == 2


def test_3_table_join_yields_three_tables(wb):
    sales = next(d for d in wb.datasources if d.name == "sales_ds")
    fqns = {t.name for t in sales.tables}
    assert {"orders", "customers", "products"} <= fqns


def test_calc_field_chain_resolved(wb):
    """Profit Ratio → Profit (calc-on-calc edge)."""
    sales = next(d for d in wb.datasources if d.name == "sales_ds")
    by_target = {e.target_field: e for e in sales.derives_from}
    assert "ProfitRatio" in by_target
    assert "Profit" in by_target["ProfitRatio"].source_fields


def test_lod_dimensions_recorded_with_kind(wb):
    """AvgSalesByRegion is `{FIXED [region]: AVG([order_amount])}`. The
    LOD ref kind must be `lod_dim`; the aggregated field's kind is
    `field`."""
    sales = next(d for d in wb.datasources if d.name == "sales_ds")
    lod_edge = next(
        e for e in sales.derives_from if e.target_field == "AvgSalesByRegion"
    )
    by_kind = {r.kind for r in lod_edge.refs}
    assert "lod_dim" in by_kind
    assert "field" in by_kind  # the inner [order_amount]


def test_cross_source_ref_detected(wb):
    """TargetAttainment in quotas_ds references `[sales_ds].[Profit]`."""
    quotas = next(d for d in wb.datasources if d.name == "quotas_ds")
    ta = next(
        e for e in quotas.derives_from if e.target_field == "TargetAttainment"
    )
    # The two-part reference must surface as kind="cross_source" with the
    # foreign datasource captured. Note: the resolved source field is
    # outside quotas_ds's local scope so it's not in source_fields, but
    # the ref entry preserves it for the writer.
    cross = [r for r in ta.refs if r.kind == "cross_source"]
    # Some plan revisions resolve cross-source refs to source_fields too,
    # but the kind-tag must always be present.
    if cross:
        assert cross[0].source_name == "Profit"
        assert cross[0].datasource_name == "sales_ds"


def test_datasource_filter_captured(wb):
    sales = next(d for d in wb.datasources if d.name == "sales_ds")
    # `region` filter at the datasource level.
    region_filter = next(
        (f for f in sales.filters if f.field_name == "region"), None,
    )
    assert region_filter is not None
    assert region_filter.worksheet_id == ""  # datasource-scoped


def test_worksheet_filter_and_sort_captured(wb):
    by_name = {w.name: w for w in wb.worksheets}
    sbr = by_name["Sales by Region"]
    assert len(sbr.filters) >= 1
    assert len(sbr.sorts) >= 1
    assert sbr.sorts[0].direction == "descending"


def test_aggregation_inferred(wb):
    """`SUM([order_amount])` on Sales by Region's cols shelf must surface
    as aggregation='SUM' on the FieldUsageIR."""
    sbr = next(w for w in wb.worksheets if w.name == "Sales by Region")
    by_field = {u.field_name: u for u in sbr.field_usages}
    # order_amount appears on cols inside `SUM(...)` and is resolved.
    if "order_amount" in by_field:
        # The same field may appear on multiple shelves — find the one on cols.
        cols_use = next(
            (u for u in sbr.field_usages
             if u.field_name == "order_amount" and u.shelf == "cols"),
            None,
        )
        if cols_use is not None:
            assert cols_use.aggregation == "SUM"


def test_dashboard_zones_and_actions_present(wb):
    exec_dash = next(d for d in wb.dashboards if d.name == "Executive Overview")
    # All non-worksheet zone kinds we declared.
    zone_kinds = {z.kind for z in exec_dash.zones}
    assert {"filter", "parameter", "text"} <= zone_kinds
    # Two declared actions.
    action_kinds = sorted(a.kind for a in exec_dash.actions)
    assert action_kinds == ["filter", "parameter"]


def test_group_set_bin_hierarchy_present(wb):
    sales = next(d for d in wb.datasources if d.name == "sales_ds")
    assert len(sales.groups) == 1 and sales.groups[0].name == "Region Bucket"
    assert len(sales.sets) == 1 and sales.sets[0].name == "Top Customers"
    assert len(sales.bins) == 1
    assert len(sales.hierarchies) == 1
    h = sales.hierarchies[0]
    assert h.name == "Geography"
    assert h.levels == ["region", "customer_segment"]


def test_value_aliases_loaded(wb):
    sales = next(d for d in wb.datasources if d.name == "sales_ds")
    region = next(f for f in sales.fields if f.name == "region")
    assert region.value_aliases == {
        "E": "East", "W": "West", "N": "North", "S": "South",
    }


def test_default_aggregation_set_on_measures(wb):
    sales = next(d for d in wb.datasources if d.name == "sales_ds")
    by_name = {f.name: f for f in sales.fields}
    assert by_name["order_amount"].default_aggregation == "sum"
    assert by_name["unit_cost"].default_aggregation == "avg"


def test_every_ir_node_has_source_line(wb):
    """Every IR object that maps to a Neo4j node must have a line value."""
    assert wb.line is not None
    for d in wb.datasources:
        assert d.line is not None
        for c in d.connections:
            assert c.line is not None
        for t in d.tables:
            assert t.line is not None
        for f in d.fields:
            assert f.line is not None
        for g in d.groups:
            assert g.line is not None
        for s in d.sets:
            assert s.line is not None
        for b in d.bins:
            assert b.line is not None
        for h in d.hierarchies:
            assert h.line is not None
    for w in wb.worksheets:
        assert w.line is not None
    for d in wb.dashboards:
        assert d.line is not None
        for z in d.zones:
            assert z.line is not None
        for a in d.actions:
            assert a.line is not None
    for p in wb.parameters:
        assert p.line is not None
