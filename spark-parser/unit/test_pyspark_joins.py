"""
join / crossJoin (plan §6 step 6).
JOINS_WITH edges + DERIVES_FROM_DATAFRAME on both inputs.
"""
import pytest


def test_left_join_captured(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("02_join_and_select.py")))

    joins = [j for df in ir.dataframes for j in df.joins]
    assert len(joins) >= 1
    assert any(j.join_type == "left" for j in joins)


def test_join_inputs_both_recorded(pyspark_fixture):
    """JOINS_WITH must record both DataFrame sides."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("02_join_and_select.py")))

    joins = [j for df in ir.dataframes for j in df.joins]
    j = joins[0]
    assert j.left in {"orders"} or "orders" in (j.left or "")
    assert j.right in {"customers"} or "customers" in (j.right or "")


def test_join_condition_string_captured(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("02_join_and_select.py")))
    joins = [j for df in ir.dataframes for j in df.joins]
    cond = joins[0].join_condition or ""
    # Best-effort textual capture
    assert "customer_id" in cond
    assert "id" in cond


def test_broadcast_hint_does_not_affect_lineage(pyspark_fixture):
    """Plan §14: broadcast() in joins is a hint — no semantic change."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))
    # The broadcast(customers) join still records 'customers' as the right side
    joins = [j for df in ir.dataframes for j in df.joins]
    rights = " ".join(j.right or "" for j in joins).lower()
    assert "customers" in rights


def test_three_way_join_emits_two_join_edges(pyspark_fixture):
    """Realistic fixture chains orders → customers → products → fx (3 joins)."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))
    joins = [j for df in ir.dataframes for j in df.joins]
    assert len(joins) >= 3


def test_join_type_inner_default():
    """`df.join(other, on)` with no `how=` argument defaults to inner."""
    from spark_parser.pyspark.visitor import parse_pyspark
    import tempfile
    src = (
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "a = spark.table('t.a'); b = spark.table('t.b')\n"
        "c = a.join(b, a.id == b.id)\n"
        "c.write.saveAsTable('t.c')\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(src); path = f.name
    ir = parse_pyspark(path)
    joins = [j for df in ir.dataframes for j in df.joins]
    assert joins[0].join_type == "inner"
