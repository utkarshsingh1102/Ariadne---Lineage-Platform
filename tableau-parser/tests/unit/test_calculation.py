"""
Calculated-field formula parsing (plan §6 step 4.4–4.5).
Build field-name → field-id map first, then resolve [bracketed] references
inside each formula to emit DerivesFromIR edges.

Plan §9.4: 100% coverage required on this module.
"""
import pytest


def test_simple_arithmetic_dependency(fixture_path):
    from tableau_parser.parser.calculation import resolve_dependencies
    from tableau_parser.parser.datasource import parse_datasources
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("02_calculated_fields.twb"))
    dses, _, _ = parse_datasources(tree)
    orders = next(d for d in dses if d.name == "orders_ds")

    deps = resolve_dependencies(orders)
    # AmountWithTax depends on Amount
    by_target = {(d.target_field, frozenset(d.source_fields)) for d in deps}
    assert ("AmountWithTax", frozenset({"Amount"})) in by_target


def test_nested_calc_chains_to_root(fixture_path):
    """Profit = [AmountWithTax] - [Amount]; AmountWithTax = [Amount] * 1.18"""
    from tableau_parser.parser.calculation import resolve_dependencies
    from tableau_parser.parser.datasource import parse_datasources
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("02_calculated_fields.twb"))
    dses, _, _ = parse_datasources(tree)
    orders = next(d for d in dses if d.name == "orders_ds")

    deps = resolve_dependencies(orders)
    profit_deps = next(d for d in deps if d.target_field == "Profit")
    assert set(profit_deps.source_fields) == {"AmountWithTax", "Amount"}


def test_lod_expression_extracted(fixture_path):
    """{FIXED [CustomerID] : SUM([Amount])} references CustomerID and Amount."""
    from tableau_parser.parser.calculation import resolve_dependencies
    from tableau_parser.parser.datasource import parse_datasources
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("02_calculated_fields.twb"))
    dses, _, _ = parse_datasources(tree)
    orders = next(d for d in dses if d.name == "orders_ds")

    deps = resolve_dependencies(orders)
    lod = next(d for d in deps if d.target_field == "TotalPerCustomer")
    assert set(lod.source_fields) == {"CustomerID", "Amount"}


def test_case_when_references_only_data_fields(fixture_path):
    """IF [Amount] > 1000 THEN 'High' ELSE 'Low' END → source = {Amount}.
    String literals must NOT be treated as field references."""
    from tableau_parser.parser.calculation import resolve_dependencies
    from tableau_parser.parser.datasource import parse_datasources
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("02_calculated_fields.twb"))
    dses, _, _ = parse_datasources(tree)
    orders = next(d for d in dses if d.name == "orders_ds")

    deps = resolve_dependencies(orders)
    tier = next(d for d in deps if d.target_field == "OrderTier")
    assert set(tier.source_fields) == {"Amount"}
    for src in tier.source_fields:
        assert src not in {"High", "Mid", "Low"}, f"literal {src!r} mis-classified"


def test_circular_reference_detected_and_skipped(tmp_path):
    """Plan §14: circular calc references — log warning, don't emit cyclic edge."""
    from tableau_parser.parser.calculation import resolve_dependencies
    from tableau_parser.parser.datasource import parse_datasources
    from tableau_parser.extractor.xml_loader import load_twb

    snip = tmp_path / "cycle.twb"
    snip.write_text("""<?xml version='1.0'?>
<workbook version='2024.1'><datasources><datasource name='ds' inline='true'>
  <connection class='teradata' dbname='X' schema='X'/>
  <column datatype='real' name='[A]' role='measure'>
    <calculation class='tableau' formula='[B] + 1'/>
  </column>
  <column datatype='real' name='[B]' role='measure'>
    <calculation class='tableau' formula='[A] + 1'/>
  </column>
</datasource></datasources></workbook>""")

    tree = load_twb(snip)
    dses, _, _ = parse_datasources(tree)
    deps = resolve_dependencies(dses[0])
    # The cyclic edge must NOT be emitted
    assert not any(
        d.target_field == "A" and "B" in d.source_fields
        and any(d2.target_field == "B" and "A" in d2.source_fields for d2 in deps)
        for d in deps
    )


def test_unresolved_reference_logged(tmp_path):
    """Plan §11: calc references a field no longer in the datasource."""
    from tableau_parser.parser.calculation import resolve_dependencies
    from tableau_parser.parser.datasource import parse_datasources
    from tableau_parser.extractor.xml_loader import load_twb

    snip = tmp_path / "missing.twb"
    snip.write_text("""<?xml version='1.0'?>
<workbook version='2024.1'><datasources><datasource name='ds' inline='true'>
  <connection class='teradata' dbname='X' schema='X'/>
  <column datatype='real' name='[Calc1]' role='measure'>
    <calculation class='tableau' formula='[DoesNotExist] * 2'/>
  </column>
</datasource></datasources></workbook>""")

    tree = load_twb(snip)
    dses, _, _ = parse_datasources(tree)
    deps = resolve_dependencies(dses[0])
    # Either no DerivesFromIR, or DerivesFromIR with empty source_fields
    calc_deps = [d for d in deps if d.target_field == "Calc1"]
    assert not calc_deps or all(not d.source_fields for d in calc_deps)
