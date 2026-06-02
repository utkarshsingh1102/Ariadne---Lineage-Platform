"""Step 6 — workbook-level cross-datasource ref resolution."""
from __future__ import annotations


def test_cross_source_ref_resolves_in_fixture_09(fixture_path):
    """Fixture 09 has ``[sales_ds].[Profit]`` referenced from
    ``TargetAttainment`` inside ``quotas_ds``. The new workbook-level
    pass must resolve that to a CrossDatasourceRefIR with both ids."""
    from tableau_parser.parser.workbook import parse_workbook
    wb = parse_workbook(str(fixture_path("09_full_reference.twb")))

    by_target_id = {r.target_field_id: r for r in wb.cross_ds_refs}
    sales = next(d for d in wb.datasources if d.name == "sales_ds")
    quotas = next(d for d in wb.datasources if d.name == "quotas_ds")
    ta_field = next(f for f in quotas.fields if f.name == "TargetAttainment")
    profit_field = next(f for f in sales.fields if f.name == "Profit")

    assert ta_field.id in by_target_id, (
        f"TargetAttainment had no resolved cross-source ref. "
        f"All refs: {wb.cross_ds_refs}"
    )
    ref = by_target_id[ta_field.id]
    assert ref.source_field_id == profit_field.id
    assert ref.source_datasource_name == "sales_ds"
    assert ref.char_start < ref.char_end
    assert "[sales_ds].[Profit]" in ref.formula_snippet or \
           ref.formula_snippet.endswith(".[Profit]")
