"""Phase 3 — schema evolution (§5) + enterprise runtime semantics (§6) tests."""
from __future__ import annotations

from spark_parser.pyspark.visitor import parse_pyspark


# ---------------------------------------------------------------------------
# §5.2 — column rename propagation
# ---------------------------------------------------------------------------

def test_rename_chain_recorded(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("rename_chain.py"))
    final = ir.dataframes[-1]
    # Two renames: amount → value → order_value
    pairs = final.renames
    assert ("value", "amount") in pairs
    assert ("order_value", "value") in pairs
    # The final field is `order_value`, the original `amount` is gone.
    field_names = {a.name for a in final.fields}
    assert "order_value" in field_names
    assert "amount" not in field_names


# ---------------------------------------------------------------------------
# §5.3 — nested schema mutation
# ---------------------------------------------------------------------------

def test_nested_paths_captured(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("nested_schema_mutation.py"))
    nested = [a for df in ir.dataframes for a in df.fields if a.path]
    paths = {a.path for a in nested}
    assert "address.city_upper" in paths
    assert "profile.contact.email_domain" in paths
    # Leaf names are still the last path segment
    leaves = {a.name for a in nested}
    assert "city_upper" in leaves
    assert "email_domain" in leaves


# ---------------------------------------------------------------------------
# §5.4 — type evolution
# ---------------------------------------------------------------------------

def test_cast_type_history_recorded(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("type_evolution.py"))
    # Find the two cast-derived attributes
    attrs = [a for df in ir.dataframes for a in df.fields if a.type_history]
    name_to_to_type = {a.name: a.type_history[-1][1] for a in attrs}
    assert name_to_to_type.get("amount_int") == "int"
    assert name_to_to_type.get("amount_double") == "double"


# ---------------------------------------------------------------------------
# §5.5 — column shadowing detection
# ---------------------------------------------------------------------------

def test_select_duplicate_alias_warns(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("column_shadowing.py"))
    matches = [
        w for w in ir.warnings
        if w.type == "column_shadowing"
        and (w.subtype or "").endswith("select_alias_duplicate")
    ]
    assert matches, "expected a select duplicate-alias info"


def test_with_column_overwrite_warns(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("column_shadowing.py"))
    matches = [
        w for w in ir.warnings
        if w.type == "column_shadowing"
        and (w.subtype or "").endswith("withColumn_overwrite")
    ]
    assert matches, "expected a withColumn overwrite info"


# ---------------------------------------------------------------------------
# §6.1 / 6.2 — cache, persist, checkpoint
# ---------------------------------------------------------------------------

def test_cache_persist_checkpoint_flags(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("cache_persist_pipeline.py"))
    by_name = {df.var_name: df for df in ir.dataframes}
    assert by_name["cached"].cached is True
    assert by_name["persisted"].cached is True
    assert by_name["persisted"].persist_level == "memory_only"
    assert by_name["checkpointed"].checkpointed is True


# ---------------------------------------------------------------------------
# §6.5 — repartition / coalesce metadata
# ---------------------------------------------------------------------------

def test_repartition_records_count_and_columns(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("cache_persist_pipeline.py"))
    by_name = {df.var_name: df for df in ir.dataframes}
    rp = by_name["repartitioned"]
    assert rp.partition_count == 8
    assert rp.partition_columns == ["customer_id"]
    co = by_name["coalesced"]
    assert co.partition_count == 2
    assert co.partition_columns == []


# ---------------------------------------------------------------------------
# §6.4 — broadcast hint propagation
# ---------------------------------------------------------------------------

def test_broadcast_wrapper_in_join_propagates(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("broadcast_hint_pipeline.py"))
    by_name = {df.var_name: df for df in ir.dataframes}
    # out_a uses broadcast(customers); out_b uses .hint("broadcast") on customers.
    assert by_name["out_a"].broadcast_hint is True
    assert by_name["out_b"].broadcast_hint is True
