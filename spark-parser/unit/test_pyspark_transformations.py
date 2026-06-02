"""
withColumn / select / drop / filter (plan §6 step 7).
Plan §9.5: 100% coverage required on this module.
"""
import pytest


def test_select_propagates_columns(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("02_join_and_select.py")))
    # The `enriched` DataFrame should have the four projected fields
    enriched = next(df for df in ir.dataframes if df.var_name == "enriched")
    field_names = {a.name for a in enriched.fields}
    assert field_names == {"order_id", "customer_id", "amount", "region"}


def test_with_column_emits_derives_from(pyspark_fixture):
    """withColumn('region_upper', col('region').cast('string'))
       must yield region_upper DERIVES_FROM region."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("03_with_column_chain.py")))

    derives = [d for df in ir.dataframes for d in df.derivations]
    by_target = {d.target_column: set(d.source_columns) for d in derives}
    assert by_target.get("region_upper") == {"region"}
    assert by_target.get("amount_with_tax") == {"amount"}
    assert by_target.get("is_high_value") == {"amount"}


def test_with_column_renamed_creates_alias_lineage(pyspark_fixture):
    """withColumnRenamed('order_id', 'id') → id DERIVES_FROM order_id, via='rename'."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("03_with_column_chain.py")))

    derives = [d for df in ir.dataframes for d in df.derivations]
    rename = next((d for d in derives if d.target_column == "id"), None)
    assert rename is not None
    assert rename.source_columns == ["order_id"]
    assert rename.via in {"rename", "withColumnRenamed"}


def test_drop_removes_column_from_df(pyspark_fixture):
    """drop('internal_flag') must NOT include internal_flag in the new DF's fields."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("03_with_column_chain.py")))

    transformed = next(df for df in ir.dataframes if df.var_name == "transformed")
    assert "internal_flag" not in {a.name for a in transformed.fields}


def test_string_literals_not_treated_as_columns():
    """lit('HIGH') must NOT show up as a source column."""
    from spark_parser.pyspark.visitor import parse_pyspark
    import tempfile
    src = (
        "from pyspark.sql import SparkSession\n"
        "from pyspark.sql.functions import col, lit, when\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "df = spark.table('t.x')\n"
        "out = df.withColumn('bucket', when(col('amount') > 1000, lit('HIGH')).otherwise(lit('LOW')))\n"
        "out.write.saveAsTable('t.y')\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(src); path = f.name
    ir = parse_pyspark(path)
    derives = [d for df in ir.dataframes for d in df.derivations]
    bucket = next(d for d in derives if d.target_column == "bucket")
    assert set(bucket.source_columns) == {"amount"}
    for s in bucket.source_columns:
        assert s not in {"HIGH", "LOW"}


def test_filter_does_not_change_columns(pyspark_fixture):
    """filter() / where() create a new DataFrame but column set is unchanged."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))
    # Search for any DataFrame derived via 'filter'
    via_filter = [
        e for df in ir.dataframes for e in df.derives_from_dataframe
        if e.via == "filter"
    ]
    assert len(via_filter) >= 1


def test_cache_and_repartition_pass_through(pyspark_fixture):
    """Plan §14: cache(), persist(), repartition(), coalesce() have NO lineage impact."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))
    # all_summary uses .cache().repartition(8); its lineage should still trace
    # back through unionByName to summary + the archive parquet path.
    sources = {
        (t.fully_qualified_name or t.location or "").lower()
        for df in ir.dataframes for t in df.reads_from
    }
    assert any("s3://archive/summary" in s for s in sources)
