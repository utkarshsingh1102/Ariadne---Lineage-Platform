"""Tests for the DataFrame collapse / transformation-chain plan
(``spark-improvement/dataframe_collapse_plan.md``).

The internal IR stays granular (``ir.dataframes`` still contains every
intermediate). The collapse layer is exposed via three new fields on
``DataFrameIR``:

  - ``is_anchor`` — True for named vars, IO sites, temp views, forks.
  - ``transform_chain`` — ordered ``TransformStepIR`` entries folded in
    from intermediate DataFrames between this anchor and its upstream.
  - ``input_anchor_ids`` — anchor :DataFrame ids feeding this node.

These tests pin each acceptance criterion from §"Acceptance criteria".
"""
from __future__ import annotations

from spark_parser.pyspark.visitor import parse_pyspark


def _ir(fixture):
    return parse_pyspark(str(fixture("collapse_anchor_chain.py")))


# ---------------------------------------------------------------------------
# Anchor classification
# ---------------------------------------------------------------------------

def test_named_assignment_is_anchor(pyspark_fixture):
    ir = _ir(pyspark_fixture)
    anchors = {df.var_name for df in ir.dataframes if df.is_anchor}
    for expected in ("df_raw", "claims", "enriched", "summary"):
        assert expected in anchors, (
            f"{expected} should be an anchor; anchors={anchors}"
        )


def test_intermediates_are_not_anchors(pyspark_fixture):
    ir = _ir(pyspark_fixture)
    anon_anchors = [
        df.var_name
        for df in ir.dataframes
        if df.is_anchor and df.var_name.startswith("__anon")
    ]
    assert not anon_anchors, (
        f"Anonymous intermediates must not be anchors; got {anon_anchors}"
    )


def test_fork_remains_its_own_anchor(pyspark_fixture):
    """``enriched`` is consumed by both ``summary`` AND a parquet write —
    plan §1: forks (≥2 downstream consumers) MUST stay an anchor.
    """
    ir = _ir(pyspark_fixture)
    enriched = next(df for df in ir.dataframes if df.var_name == "enriched")
    assert enriched.is_anchor


# ---------------------------------------------------------------------------
# transform_chain content
# ---------------------------------------------------------------------------

def test_claims_chain_records_every_intermediate_op(pyspark_fixture):
    """``claims = df_raw.dropDuplicates(...).withColumn(...).filter(...)``
    collapses three intermediate ops into ``claims.transform_chain`` in
    source order.
    """
    ir = _ir(pyspark_fixture)
    claims = next(df for df in ir.dataframes if df.var_name == "claims")
    ops = [s.op for s in claims.transform_chain]
    assert ops == ["dropDuplicates", "withColumn", "filter"], ops
    # Sequence numbers are monotonic.
    assert [s.seq for s in claims.transform_chain] == [0, 1, 2]


def test_withColumn_step_records_expression_and_columns(pyspark_fixture):
    ir = _ir(pyspark_fixture)
    claims = next(df for df in ir.dataframes if df.var_name == "claims")
    wc = next(s for s in claims.transform_chain if s.op == "withColumn")
    assert wc.kind == "derive"
    assert wc.output_column == "denied"
    assert "paid_amount" in wc.input_columns
    assert wc.expr and "paid_amount" in wc.expr


def test_filter_step_carries_predicate_columns(pyspark_fixture):
    ir = _ir(pyspark_fixture)
    claims = next(df for df in ir.dataframes if df.var_name == "claims")
    f = next(s for s in claims.transform_chain if s.op == "filter")
    assert f.kind == "filter"
    assert "claim_id" in f.input_columns
    assert f.expr is not None


def test_input_anchor_ids_point_at_upstream_anchor(pyspark_fixture):
    """``claims`` reads from ``df_raw``; after collapse its
    ``input_anchor_ids`` must reference ``df_raw.id``.
    """
    ir = _ir(pyspark_fixture)
    df_raw = next(df for df in ir.dataframes if df.var_name == "df_raw")
    claims = next(df for df in ir.dataframes if df.var_name == "claims")
    assert df_raw.id in claims.input_anchor_ids


def test_summary_chain_includes_groupby_and_agg(pyspark_fixture):
    ir = _ir(pyspark_fixture)
    summary = next(df for df in ir.dataframes if df.var_name == "summary")
    ops = [s.op for s in summary.transform_chain]
    assert "groupBy" in ops
    assert "agg" in ops
    agg = next(s for s in summary.transform_chain if s.op == "agg")
    assert "total_paid" in agg.output_columns


# ---------------------------------------------------------------------------
# Invariants from plan §6
# ---------------------------------------------------------------------------

def test_anchor_count_matches_named_anchors(pyspark_fixture):
    """Plan acceptance: dataframes count = named anchors (not 46).
    On this fixture there are exactly 4 named anchors (df_raw, claims,
    enriched, summary).
    """
    ir = _ir(pyspark_fixture)
    anchor_names = sorted(df.var_name for df in ir.dataframes if df.is_anchor)
    assert anchor_names == ["claims", "df_raw", "enriched", "summary"], anchor_names


def test_column_count_matches_resolved_fields(pyspark_fixture):
    ir = _ir(pyspark_fixture)
    for df in ir.dataframes:
        if not df.is_anchor:
            continue
        # column_count (set by the collapse pass) tracks the resolved
        # output schema length.
        assert df.column_count == len(df.fields), df.var_name


def test_granular_ir_still_populated(pyspark_fixture):
    """Plan §6 — "No information loss". The granular intermediate
    DataFrames must still be present in ``ir.dataframes`` so internal
    consumers can reconstruct fine-grained column lineage.
    """
    ir = _ir(pyspark_fixture)
    intermediates = [df for df in ir.dataframes if not df.is_anchor]
    assert intermediates, (
        "Granular intermediates were dropped — collapse must be display-only"
    )
