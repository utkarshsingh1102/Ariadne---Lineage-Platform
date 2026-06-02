"""Token-level formula reference extraction (target plan Step 3).

Spans are inclusive of the surrounding brackets so the View Source panel
can highlight the visible token rather than just its inner name.
"""
from __future__ import annotations


def test_find_refs_with_spans_basic():
    from tableau_parser.utils.brackets import find_refs_with_spans

    spans = find_refs_with_spans("[A]+[B]*100")
    assert spans == [("A", 0, 3), ("B", 4, 7)]


def test_find_refs_with_spans_duplicates_preserved():
    from tableau_parser.utils.brackets import find_refs_with_spans

    spans = find_refs_with_spans("[A]+[A]")
    assert spans == [("A", 0, 3), ("A", 4, 7)]


def test_find_cross_source_refs_detected():
    from tableau_parser.utils.brackets import find_cross_source_refs

    spans = find_cross_source_refs("[ds2].[fieldX] + [B]")
    # Whole `[ds2].[fieldX]` span recorded as one tuple.
    assert spans == [("ds2", "fieldX", 0, 14)]


def test_find_lod_dimensions_extracted():
    from tableau_parser.utils.brackets import find_lod_dimensions

    spans = find_lod_dimensions("{FIXED [Region],[Segment]: AVG([Sales])}")
    assert ("FIXED", "Region") in [(k, n) for k, n, _, _ in spans]
    assert ("FIXED", "Segment") in [(k, n) for k, n, _, _ in spans]
    # The measure `[Sales]` is OUTSIDE the dimension list — not returned by
    # the LOD-dimension finder. (It will be picked up as a plain field ref.)
    assert not any(n == "Sales" for _, n, _, _ in spans)


def test_find_lod_dimensions_handles_include_and_exclude():
    from tableau_parser.utils.brackets import find_lod_dimensions

    inc = find_lod_dimensions("{INCLUDE [Cust]: AVG([X])}")
    exc = find_lod_dimensions("{EXCLUDE [Date]: SUM([X])}")
    assert [(k, n) for k, n, _, _ in inc] == [("INCLUDE", "Cust")]
    assert [(k, n) for k, n, _, _ in exc] == [("EXCLUDE", "Date")]


def test_calc_refs_populated_for_simple_formula(fixture_path):
    """Fixture 02 has a calc field `Profit` = `[AmountWithTax] - [Cost]`.
    Each token in that formula must produce a FormulaRefIR with a non-zero
    span pointing at the right name.
    """
    from tableau_parser.parser.workbook import parse_workbook

    wb = parse_workbook(fixture_path("02_calculated_fields.twb"))
    all_edges = [e for d in wb.datasources for e in d.derives_from]
    profit = next((e for e in all_edges if e.target_field == "Profit"), None)
    assert profit is not None, "fixture 02 has a Profit calc"
    assert profit.refs, "Profit's refs list must be populated"
    # Every ref points at one of the formula's source names and has a
    # non-degenerate span.
    for ref in profit.refs:
        assert ref.source_name in profit.source_fields + ["AmountWithTax", "Cost", "Amount"]
        assert ref.char_end > ref.char_start
        assert ref.kind in {"field", "param", "cross_source", "lod_dim"}


def test_calc_refs_kind_default_is_field():
    from tableau_parser.parser.calculation import _collect_refs

    refs = _collect_refs("[A]+[B]", target_name="C")
    assert all(r.kind == "field" for r in refs)
    assert [(r.source_name, r.char_start, r.char_end) for r in refs] == [
        ("A", 0, 3), ("B", 4, 7),
    ]


def test_calc_refs_lod_kind_assigned():
    from tableau_parser.parser.calculation import _collect_refs

    refs = _collect_refs("{FIXED [Region]: AVG([Sales])}", target_name="X")
    # `[Region]` is a LOD dimension; `[Sales]` is a plain field ref outside
    # the dimension list.
    by_name = {r.source_name: r for r in refs}
    assert by_name["Region"].kind == "lod_dim"
    assert by_name["Sales"].kind == "field"


def test_calc_refs_cross_source_kind_assigned():
    from tableau_parser.parser.calculation import _collect_refs

    refs = _collect_refs("[ds2].[fieldX] + [B]", target_name="X")
    by_name = {r.source_name: r for r in refs}
    assert by_name["fieldX"].kind == "cross_source"
    assert by_name["fieldX"].datasource_name == "ds2"
    assert by_name["B"].kind == "field"


def test_calc_refs_self_ref_skipped():
    from tableau_parser.parser.calculation import _collect_refs

    # Even though [X] appears, it's the target — don't emit a self-edge.
    refs = _collect_refs("[X] + [Y]", target_name="X")
    names = {r.source_name for r in refs}
    assert names == {"Y"}
