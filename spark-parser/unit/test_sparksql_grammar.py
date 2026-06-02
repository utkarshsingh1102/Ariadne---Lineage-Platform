"""v0.2 §4 — advanced Spark SQL grammar lineage tests."""
from __future__ import annotations

from pathlib import Path

from spark_parser.sparksql.lineage import extract_lineage

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sparksql" / "grammar"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_recursive_cte_emits_self_loop_derivations():
    ir = extract_lineage(_read("recursive_cte.sql"))
    recursive = [d for d in ir.derivations if d.via == "recursive_cte"]
    assert recursive, "expected at least one recursive_cte derivation"
    cols = {d.target_column for d in recursive}
    # The CTE projects employee_id, manager_id, depth — recursion self-loops them.
    assert "employee_id" in cols
    assert "manager_id" in cols
    assert "depth" in cols
    # Self-loop: source column equals target column for recursive_cte derivations.
    for d in recursive:
        assert d.source_columns == [d.target_column]


def test_recursive_cte_does_not_emit_cte_as_source():
    """The recursive CTE's name (`org_tree`) must not leak as a physical
    source table; sources stay confined to actual base tables.
    """
    ir = extract_lineage(_read("recursive_cte.sql"))
    src_lower = {s.lower() for s in ir.source_tables}
    assert "org_tree" not in src_lower


def test_lateral_view_emits_derivation_per_output_column():
    ir = extract_lineage(_read("lateral_view.sql"))
    lat = [d for d in ir.derivations if d.via in {"lateral_view", "lateral_view_outer"}]
    assert lat, "expected LATERAL VIEW derivations"
    tgts = {d.target_column for d in lat}
    assert "exploded_tag" in tgts
    assert "tag_pos" in tgts


def test_lateral_view_outer_is_classified_distinctly():
    ir = extract_lineage(_read("lateral_view.sql"))
    outer = [d for d in ir.derivations if d.via == "lateral_view_outer"]
    plain = [d for d in ir.derivations if d.via == "lateral_view"]
    assert outer, "expected lateral_view_outer for the OUTER variant"
    assert plain, "expected lateral_view for the non-OUTER variant"


def test_correlated_subquery_classified():
    ir = extract_lineage(_read("correlated_subquery.sql"))
    corr = [d for d in ir.derivations if d.via == "correlated_subquery"]
    assert corr, "expected a correlated_subquery derivation"
    target = next(d for d in corr if d.target_column == "max_amount")
    # source_columns should reference at least the inner aggregated column.
    assert "amount" in target.source_columns


def test_scalar_subquery_classified():
    ir = extract_lineage(_read("scalar_subquery.sql"))
    scalar = [d for d in ir.derivations if d.via == "scalar_subquery"]
    assert scalar, "expected a scalar_subquery derivation"
    target = next(d for d in scalar if d.target_column == "global_max")
    assert "amount" in target.source_columns


def test_window_functions_classified():
    ir = extract_lineage(_read("window_edge_cases.sql"))
    win = [d for d in ir.derivations if d.via == "window"]
    targets = {d.target_column for d in win}
    assert "rn" in targets
    assert "rk" in targets
    assert "amount_rolling_7d" in targets
