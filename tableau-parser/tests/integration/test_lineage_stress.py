"""Improvement-v2 §8 — full-stack regression against the stress fixture.

The fixture was authored to break naive parsers: ``_.fcp.*...`` prefixed
relations, stored procs, federated cross-DB joins, CTEs, chained calcs,
LOD nesting, table calcs, field-name collisions across datasources, data
blending, drill paths, escaped-bracket identifiers, and Unicode.

Each block below pins one of the 11 fixes from improvement-v2.md.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def wb(fixture_path):
    from tableau_parser.parser.workbook import parse_workbook
    return parse_workbook(str(fixture_path("11_lineage_stress.twb")))


# ---- Golden manifest ---------------------------------------------------

EXPECTED = {
    "datasources": 2,
    "parameter_scopes": 1,
    "tables": 4,
    "calculated_fields": 11,
    "worksheets": 2,
    "dashboards": 1,
    "parameters": 3,
}


@pytest.mark.parametrize("key", list(EXPECTED))
def test_golden_manifest(wb, key):
    assert wb.stats()[key] == EXPECTED[key], (
        f"{key}: got {wb.stats()[key]}, expected {EXPECTED[key]}"
    )


# ---- Fix 1: prefix normalization ---------------------------------------

def test_fcp_prefixed_relation_now_visible(wb):
    """``_.fcp.ObjectModelEncapsulateLegacy.false...relation`` wraps the
    custom-SQL CTE block. Pre-fix the parser saw zero custom_sql tables
    because the prefixed tag was invisible. The fix normalises to
    ``relation`` so the relation walker dispatches correctly."""
    sales = next(d for d in wb.datasources if d.name == "federated.0sales1xyz")
    custom_sql_tables = [t for t in sales.tables if t.relation_type == "custom_sql"]
    # The CTE Custom SQL Query selects from analytics.public.orders and
    # analytics.public.customers — both surface as physical FQNs.
    fqns = {t.fully_qualified_name for t in custom_sql_tables}
    assert "ANALYTICS.PUBLIC.ORDERS" in fqns
    assert "ANALYTICS.PUBLIC.CUSTOMERS" in fqns


def test_no_fcp_unmapped_warnings(wb):
    """Once the prefix is normalised, FCP feature-flag tags (MarkAnimation
    etc.) land on the IGNORE list. No unmapped_element warnings remain."""
    assert wb.warnings == [], f"unexpected warnings: {wb.warnings}"


# ---- Fix 2: stored proc ------------------------------------------------

def test_stored_proc_emits_table_with_proc_relation_type(wb):
    returns = next(d for d in wb.datasources if d.name == "sqlproxy.0returns2abc")
    procs = [t for t in returns.tables if t.relation_type == "stored_proc"]
    assert len(procs) == 1
    assert procs[0].name == "usp_returns_summary"
    assert procs[0].schema == "dbo"


def test_stored_proc_declared_columns_become_fields(wb):
    """The 3 ``<columns>/<column>`` children of the proc relation land as
    physical FieldIRs scoped to the Returns SP datasource."""
    returns = next(d for d in wb.datasources if d.name == "sqlproxy.0returns2abc")
    by_name = {f.name: f for f in returns.fields}
    for n in ("order_id", "net_amount", "region_code", "return_rate"):
        assert n in by_name, f"proc column {n} missing"
        assert not by_name[n].is_calculated


# ---- Fix 3a: raw_sql ---------------------------------------------------

def test_custom_sql_raw_sql_persisted(wb):
    sales = next(d for d in wb.datasources if d.name == "federated.0sales1xyz")
    cs_tables = [t for t in sales.tables if t.relation_type == "custom_sql"]
    # At least one custom-SQL table carries the raw body. (The relation
    # walker stamps the same body on every FQN extracted from it.)
    assert any(t.raw_sql.strip() for t in cs_tables)
    sample = next(t for t in cs_tables if t.raw_sql)
    assert "WITH regional_orders AS" in sample.raw_sql


# ---- Fix 4: parameter scope --------------------------------------------

def test_parameters_block_emits_scope_not_datasource(wb):
    assert len(wb.parameter_scopes) == 1
    assert wb.parameter_scopes[0].name == "Parameters"
    # User-datasource count is honest: only the two real ones.
    ds_names = {d.name for d in wb.datasources}
    assert ds_names == {"federated.0sales1xyz", "sqlproxy.0returns2abc"}


# ---- Fix 5: calc-class branching ---------------------------------------

def test_only_class_tableau_counts_as_calc_field(wb):
    """11 expected calc fields. The bin column (``class='bin'``) and the
    set column (``class='categorical-bin'``) must NOT count."""
    calc_names = sorted(
        f.name for d in wb.datasources for f in d.fields if f.is_calculated
    )
    forbidden = {"net_amount (bin)", "Set 1"}
    assert forbidden.isdisjoint(calc_names), (
        f"non-tableau calcs leaked into calc fields: {forbidden & set(calc_names)}"
    )
    assert len(calc_names) == 11


def test_bin_and_set_land_in_derived_lists(wb):
    sales = next(d for d in wb.datasources if d.name == "federated.0sales1xyz")
    assert len(sales.bins) == 1
    assert sales.bins[0].name == "net_amount (bin)"
    assert len(sales.sets) == 1
    assert sales.sets[0].name == "Set 1"


# ---- Fix 6: cross-source resolution ------------------------------------

def test_parameter_ref_resolves_as_cross_ds(wb):
    """``Calculation_net_risk`` references ``[Parameters].[Parameter 2]``.
    The resolver must produce one CrossDatasourceRefIR pointing at the
    Parameter id."""
    sales = next(d for d in wb.datasources if d.name == "federated.0sales1xyz")
    net_risk = next(f for f in sales.fields if f.name == "Calculation_net_risk")
    param2 = next(p for p in wb.parameters if p.name == "Parameter 2")
    refs = [
        r for r in wb.cross_ds_refs
        if r.target_field_id == net_risk.id and r.source_field_id == param2.id
    ]
    assert len(refs) == 1
    assert refs[0].source_datasource_name == "Parameters"


def test_collided_field_name_does_not_leak_across_datasources(wb):
    """``net_amount`` exists in both datasources. The in-DS resolution is
    structurally scoped per call, but verify there are no cross-DS refs
    pointing at the *wrong* net_amount (Sales DS isn't a calc)."""
    sales = next(d for d in wb.datasources if d.name == "federated.0sales1xyz")
    returns = next(d for d in wb.datasources if d.name == "sqlproxy.0returns2abc")
    return_loss = next(
        f for f in returns.fields if f.name == "Calculation_return_loss"
    )
    edge = next(e for e in returns.derives_from if e.target_field == "Calculation_return_loss")
    # The in-datasource derives_from must list net_amount and return_rate
    # — both Returns SP fields.
    assert "net_amount" in edge.source_fields
    assert "return_rate" in edge.source_fields
    # And we must NOT have emitted a cross-DS ref pointing at Sales DS's
    # net_amount.
    sales_net = next(f for f in sales.fields if f.name == "net_amount") if any(
        f.name == "net_amount" for f in sales.fields
    ) else None
    if sales_net is not None:
        leakage = [
            r for r in wb.cross_ds_refs
            if r.target_field_id == return_loss.id
            and r.source_field_id == sales_net.id
        ]
        assert leakage == [], "net_amount leaked across datasources"


# ---- Fix 7 / 8 / 10: LOD, table calcs, hierarchies (already correct) ---

def test_lod_dimensions_surface_alongside_inner_refs(wb):
    """``{ FIXED [customer_id] : SUM([Calculation_net_usd]) }`` — the
    dimension AND the inner field both come through as FormulaRefIR rows."""
    sales = next(d for d in wb.datasources if d.name == "federated.0sales1xyz")
    edge = next(
        e for e in sales.derives_from if e.target_field == "Calculation_lod_fixed"
    )
    kinds = {(r.kind, r.source_name) for r in edge.refs}
    assert ("lod_dim", "customer_id") in kinds
    assert ("field", "Calculation_net_usd") in kinds


def test_hierarchy_levels_preserve_order(wb):
    sales = next(d for d in wb.datasources if d.name == "federated.0sales1xyz")
    assert len(sales.hierarchies) == 1
    h = sales.hierarchies[0]
    assert h.name == "Geo Time Drill"
    assert h.levels == ["region_code", "Calculation_date_bucket", "customer_id"]


# ---- Fix 9: data blending ----------------------------------------------

def test_worksheet_blend_extracted(wb):
    risk = next(w for w in wb.worksheets if w.name == "Risk Overview")
    assert len(risk.blends) == 1
    bl = risk.blends[0]
    assert bl.primary_datasource_name == "federated.0sales1xyz"
    assert bl.secondary_datasource_name == "sqlproxy.0returns2abc"
    assert bl.on_field_names == ["region_code"]


# ---- Fix 11: bracket-escape identifier ---------------------------------

def test_escaped_bracket_field_resolves_as_one_token(wb):
    """Calculation_margen_unicode's formula ``SUM([net_amount]) /
    SUM([gross_amount_]]raw])`` contains an escaped ``]]``. Pre-fix the
    tokeniser split it into ``gross_amount_`` + ``raw``. After the fix it
    must surface as a single token ``gross_amount_]raw``.
    """
    from tableau_parser.utils.brackets import find_refs
    sales = next(d for d in wb.datasources if d.name == "federated.0sales1xyz")
    margen = next(
        f for f in sales.fields if f.name == "Calculation_margen_unicode"
    )
    refs = find_refs(margen.formula)
    assert "gross_amount_]raw" in refs
    # And the broken halves must NOT appear.
    assert "gross_amount_" not in refs
    assert "raw" not in refs
