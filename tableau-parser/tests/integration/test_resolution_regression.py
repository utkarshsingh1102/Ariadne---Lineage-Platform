"""Regression suite for RESOLUTION_PLAN.md §2.

Each test pins a single defect signature so a future regression points at
the precise section to re-read. Run against ``10_resolution_regression.twb``
which is shaped to fail in exactly the pre-fix way: nested datasource
references, a parameter masquerading as a calc field, and metadata-record
leaf children. The plan's §1 "golden manifest" idea is realised here as
the per-key assertions below.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def wb(fixture_path):
    from tableau_parser.parser.workbook import parse_workbook
    return parse_workbook(str(fixture_path("10_resolution_regression.twb")))


# ---- §2.1 --------------------------------------------------------------

def test_no_scope_leak_from_worksheet_datasource_refs(wb):
    """Pre-fix counted 1 (sales_ds) + 2 worksheet refs = 3. After the
    anchored path lands only the top-level definition."""
    assert len(wb.datasources) == 1
    assert wb.datasources[0].name == "sales_ds"


# ---- §2.2 --------------------------------------------------------------

def test_parameter_in_user_datasource_isolated(wb):
    """The ``[Min Sales]`` column in sales_ds carries
    ``param-domain-type='range'`` — it must surface in
    ``workbook.parameters``, NOT in ``sales_ds.fields``."""
    by_param_name = {p.name for p in wb.parameters}
    assert "Min Sales" in by_param_name          # captured as parameter
    assert "Region Filter" in by_param_name      # the Parameters DS one
    assert len(wb.parameters) == 2

    sales = wb.datasources[0]
    field_names = {f.name for f in sales.fields}
    assert "Min Sales" not in field_names        # not double-counted as a field


def test_real_calc_field_still_counted(wb):
    """The §2.2 filter must not over-reach. ``[Profit]`` is a genuine calc
    field — no ``param-domain-type`` — and must remain in the calc count."""
    sales = wb.datasources[0]
    calcs = [f for f in sales.fields if f.is_calculated]
    assert [c.name for c in calcs] == ["Profit"]


# ---- §2.4 --------------------------------------------------------------

def test_metadata_record_children_not_unmapped(wb):
    """remote-name / local-name / parent-name / local-type appear as
    ``<metadata-record>`` children. Before §2.4 each emitted one
    unmapped_element warning; the list must now be empty for these tags."""
    leaks = {"remote-name", "local-name", "parent-name", "local-type",
             "remote-type", "aggregation", "contains-null"}
    unmapped_tags = {w.get("detail") for w in wb.warnings
                     if w.get("type") == "unmapped_element"}
    assert leaks.isdisjoint(unmapped_tags), (
        f"metadata-record children leaked as unmapped: {leaks & unmapped_tags}"
    )


# ---- Golden manifest (plan §1) -----------------------------------------

EXPECTED = {
    "datasources": 1,
    "tables": 1,
    "raw_columns": 2,        # order_id, order_amount  (Min Sales is a param)
    "calculated_fields": 1,  # Profit
    "parameters": 2,         # Region Filter + Min Sales
    "worksheets": 2,
    "dashboards": 0,
}


@pytest.mark.parametrize("key", list(EXPECTED))
def test_golden_counts(wb, key):
    sales = wb.datasources[0] if wb.datasources else None
    actual = {
        "datasources": len(wb.datasources),
        "tables": sum(len(d.tables) for d in wb.datasources),
        "raw_columns": sum(
            1 for d in wb.datasources for f in d.fields if not f.is_calculated
        ),
        "calculated_fields": sum(
            1 for d in wb.datasources for f in d.fields if f.is_calculated
        ),
        "parameters": len(wb.parameters),
        "worksheets": len(wb.worksheets),
        "dashboards": len(wb.dashboards),
    }[key]
    assert actual == EXPECTED[key], (
        f"{key}: got {actual}, expected {EXPECTED[key]}"
    )
