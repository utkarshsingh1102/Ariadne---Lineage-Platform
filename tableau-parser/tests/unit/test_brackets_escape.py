"""Step 8a — ``]]`` is a literal ``]`` inside a Tableau identifier."""
from __future__ import annotations

from tableau_parser.utils.brackets import (
    find_cross_source_refs,
    find_lod_dimensions,
    find_refs,
    find_refs_with_spans,
    strip_brackets,
)


def test_strip_brackets_unescapes_doubled_bracket():
    assert strip_brackets("[gross_amount_]]raw]") == "gross_amount_]raw"


def test_find_refs_returns_one_token_for_escaped_bracket():
    """Pre-fix would split into ``["gross_amount_", "raw"]``."""
    refs = find_refs("SUM([gross_amount_]]raw])")
    assert refs == ["gross_amount_]raw"]


def test_find_refs_with_spans_keeps_span_over_the_full_token():
    formula = "SUM([gross_amount_]]raw])"
    spans = find_refs_with_spans(formula)
    assert len(spans) == 1
    name, start, end = spans[0]
    assert name == "gross_amount_]raw"
    # Span spans the full bracketed token, inclusive of the brackets.
    assert formula[start:end] == "[gross_amount_]]raw]"


def test_normal_bracket_refs_unaffected():
    """Sanity — the regex still matches plain identifiers cleanly."""
    assert find_refs("[A]+[B]") == ["A", "B"]
    spans = find_refs_with_spans("[A]+[B]")
    assert [s[0] for s in spans] == ["A", "B"]


def test_cross_source_ref_with_escaped_bracket_in_field():
    refs = find_cross_source_refs("[ds].[gross_amount_]]raw]")
    assert len(refs) == 1
    ds, field, _, _ = refs[0]
    assert ds == "ds"
    assert field == "gross_amount_]raw"


def test_lod_dimensions_unescape_field_name():
    formula = "{FIXED [region_]]label] : AVG([sales])}"
    out = find_lod_dimensions(formula)
    # Only the dimension refs come through; the inner aggregated field is
    # picked up by find_refs separately.
    assert ("FIXED", "region_]label") in [(k, n) for k, n, _, _ in out]
