"""Regression-tests for the M2/M3 gaps documented in
`spark-improvement/plan_1.md`. Each test pins behaviour for one
fixture so the gap can't silently re-open.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# M3a — selectExpr SQL string parsing
# ---------------------------------------------------------------------------

def test_selectexpr_extracts_source_columns(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("selectexpr_columns.py")))

    derives = [d for df in ir.dataframes for d in df.derivations]
    by_target = {d.target_column: set(d.source_columns) for d in derives}

    # "amount * 1.18 AS taxed_amount" → taxed_amount derives from amount
    assert by_target.get("taxed_amount") == {"amount"}
    # CASE WHEN status = 'PAID' ... → is_paid derives from status
    assert by_target.get("is_paid") == {"status"}
    # concat(country, '-', region) AS market → market depends on country+region
    assert by_target.get("market") == {"country", "region"}


def test_selectexpr_via_label(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("selectexpr_columns.py")))
    derives = [d for df in ir.dataframes for d in df.derivations]
    expr_derivs = [d for d in derives if d.via == "selectExpr"]
    assert expr_derivs, "selectExpr derivations should be tagged via='selectExpr'"


# ---------------------------------------------------------------------------
# M3b — star projection + chained alias
# ---------------------------------------------------------------------------

def test_select_star_emits_star_marker(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("star_select_and_alias_chain.py")))

    derives = [d for df in ir.dataframes for d in df.derivations]
    star_markers = [d for d in derives if d.target_column == "*"]
    assert star_markers, "select('*') should emit a star-marker derivation"
    assert star_markers[0].source_columns == ["*"]


def test_chained_alias_records_intermediate(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("star_select_and_alias_chain.py")))

    derives = [d for df in ir.dataframes for d in df.derivations]
    intermediates = [d for d in derives if d.via == "alias_chain"]
    targets = {d.target_column for d in intermediates}
    # Inner alias `raw_amt` must be recorded; outer `amt` is the field name.
    assert "raw_amt" in targets


# ---------------------------------------------------------------------------
# M2a — recursion + variadic args
# ---------------------------------------------------------------------------

def test_self_recursion_marked_recursive(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("recursion_and_vararg.py")))

    warning_types = {w.type for w in ir.warnings}
    # M2b-detected pre-pass should refuse to inline `looper` and emit this.
    assert "recursive_function" in warning_types


def test_vararg_helper_warns(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("recursion_and_vararg.py")))

    warning_types = {w.type for w in ir.warnings}
    assert "interproc_vararg" in warning_types


# ---------------------------------------------------------------------------
# M2a — tuple return + tuple LHS
# ---------------------------------------------------------------------------

def test_tuple_return_partial_warning(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("tuple_return.py")))

    warning_types = {w.type for w in ir.warnings}
    assert "tuple_return_partial" in warning_types


def test_tuple_lhs_first_target_bound(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("tuple_return.py")))

    var_names = {df.var_name for df in ir.dataframes}
    # First LHS must be bound; second is documented partial.
    assert "paid_df" in var_names


# ---------------------------------------------------------------------------
# Try-block entry + type-constructor false-positive
# ---------------------------------------------------------------------------

def test_try_block_main_entry_is_visited(pyspark_fixture):
    """``if __name__ == "__main__": try: metrics = run_pipeline(spark)`` —
    the visitor must descend into the try body or the entry call (and
    everything inlined from it) is silently dropped.
    """
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("try_main_entry.py")))

    var_names = {df.var_name for df in ir.dataframes}
    # If the try-body got visited, run_pipeline → read_orders inlined and
    # df_orders + df_clean are bound in the caller's scope.
    assert "df_orders" in var_names or "df_clean" in var_names, (
        f"Expected run_pipeline body to be visited; only saw {var_names}"
    )


def test_type_constructors_not_classified_as_dataframes(pyspark_fixture):
    """``SCHEMA = StructType([...])`` is a schema literal, not a DataFrame.

    The unknown-call heuristic must reject it so the IR stays clean.
    """
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("try_main_entry.py")))

    var_names = {df.var_name for df in ir.dataframes}
    assert "ORDER_SCHEMA" not in var_names, (
        "StructType(...) was wrongly emitted as a DataFrame"
    )


# ---------------------------------------------------------------------------
# Interprocedural call-binding: kwargs, defaults, dynamic sink re-resolution
# ---------------------------------------------------------------------------

def test_kwargs_do_not_trigger_false_args_mismatch(pyspark_fixture):
    """``write_delta(df, path, partition_by=[…], z_order=…)`` and
    ``read_jdbc(spark, table="public.x")`` — every required param is bound
    via positional or keyword, defaults cover the rest. The old binder
    raised 9 false ``interproc_args_mismatch`` warnings; the new binder
    must raise zero.
    """
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("kwarg_binding_pipeline.py")))
    bogus = [w for w in ir.warnings if w.type == "interproc_args_mismatch"]
    assert not bogus, (
        f"Expected zero interproc_args_mismatch warnings, got "
        f"{[w.detail for w in bogus]}"
    )


def test_write_partition_columns_attached_to_sink(pyspark_fixture):
    """``partition_by=["order_year","order_month"]`` becomes the
    WriteEdgeIR.partition_columns on the gold-orders sink.
    """
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("kwarg_binding_pipeline.py")))
    by_location: dict[str, list[str]] = {}
    for df in ir.dataframes:
        for edge in df.write_edges:
            loc = edge.target.location or edge.target.fully_qualified_name
            if loc:
                by_location[loc] = list(edge.partition_columns)
    assert by_location.get("s3a://gold/orders/") == ["order_year", "order_month"], (
        f"Expected partition_by to flow onto gold/orders/ sink; got {by_location}"
    )
    assert by_location.get("s3a://gold/customers/") == ["country_code"], (
        f"Expected partition_by=country_code on gold/customers/ sink; got "
        f"{by_location}"
    )


def test_z_order_columns_captured_on_sink(pyspark_fixture):
    """``z_order="customer_id,product_id"`` is split into a list and
    attached to the WriteEdgeIR.z_order_columns of the corresponding sink.
    """
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("kwarg_binding_pipeline.py")))
    z_by_location: dict[str, list[str]] = {}
    for df in ir.dataframes:
        for edge in df.write_edges:
            loc = edge.target.location or edge.target.fully_qualified_name
            if loc and edge.z_order_columns:
                z_by_location[loc] = list(edge.z_order_columns)
    assert z_by_location.get("s3a://gold/orders/") == ["customer_id", "product_id"]
    assert z_by_location.get("s3a://gold/customers/") == ["customer_id"]


def test_save_target_resolves_per_call_site(pyspark_fixture):
    """``writer.save(path)`` inside the inlined ``write_delta`` must
    resolve ``path`` against the current call site's binding
    (``cfg.GOLD_ORDERS`` vs ``cfg.GOLD_CUSTOMERS``) — NOT emit a
    ``dynamic_table_name`` warning.
    """
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("kwarg_binding_pipeline.py")))
    dyn = [w for w in ir.warnings if w.type == "dynamic_table_name"]
    assert not dyn, (
        f"Expected zero dynamic_table_name; got {[w.detail for w in dyn]}"
    )
    locations = {
        edge.target.location
        for df in ir.dataframes for edge in df.write_edges
        if edge.target.location
    }
    expected = {"s3a://gold/orders/", "s3a://gold/customers/"}
    assert expected <= locations, (
        f"Expected both per-call save() paths resolved; got {locations}"
    )


def test_read_jdbc_kwarg_becomes_dbtable(pyspark_fixture):
    """``read_jdbc(spark, table="public.customers")`` — ``table`` is a
    kwarg consumed inside ``.option("dbtable", table)``. After binding,
    the JDBC dataset name should be the literal table.
    """
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("kwarg_binding_pipeline.py")))
    jdbc_targets = {
        t.fully_qualified_name
        for df in ir.dataframes for t in df.reads_from
        if (t.storage_format or "").lower() == "jdbc"
    }
    assert "public.orders" in jdbc_targets, jdbc_targets
    assert "public.customers" in jdbc_targets, jdbc_targets


# ---------------------------------------------------------------------------
# column_shadowing — info severity + self-referential derivation
# ---------------------------------------------------------------------------

def test_with_column_shadow_emits_self_edge(pyspark_fixture):
    """In-place ``withColumn("risk_label", …)`` overwrites must:
    * downgrade the warning subtype to ``info:withColumn_overwrite``
    * append a self-edge derivation so the old column → new column
      lineage is preserved even when the user expression doesn't name it.
    """
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("column_shadow_self_edge.py")))

    shadow_warnings = [
        w for w in ir.warnings if w.type == "column_shadowing"
    ]
    assert shadow_warnings, "expected at least one column_shadowing warning"
    assert all((w.subtype or "").startswith("info:") for w in shadow_warnings), (
        f"All column_shadowing warnings must be info-severity; got "
        f"{[w.subtype for w in shadow_warnings]}"
    )

    self_edges = [
        d
        for df in ir.dataframes for d in df.derivations
        if d.via == "withColumn_shadow"
        and d.target_column == "risk_label"
        and d.source_columns == ["risk_label"]
    ]
    assert self_edges, (
        "expected self-referential derivation risk_label → risk_label"
    )
