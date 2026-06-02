"""
PySpark source detection (plan §6 step 6).
spark.read.X, spark.table, spark.sql, JDBC.
"""
import pytest


def test_parquet_load_creates_source_table(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("01_simple_read_write.py")))

    src_tables = [t for df in ir.dataframes for t in df.reads_from]
    assert any(t.storage_format == "parquet" for t in src_tables)
    assert any(t.location == "s3://raw/orders/" or t.location == "s3://raw/orders" for t in src_tables)


def test_spark_table_creates_hive_table(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("02_join_and_select.py")))

    fqns = {t.fully_qualified_name.lower() for df in ir.dataframes for t in df.reads_from}
    assert "prod.dim.customers" in fqns


def test_spark_table_with_default_database(tmp_path, monkeypatch):
    """Plan §8: spark.table("orders") with no qualification → default.orders."""
    from spark_parser.pyspark.visitor import parse_pyspark
    monkeypatch.setenv("DEFAULT_DATABASE", "default")

    f = tmp_path / "x.py"
    f.write_text(
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "df = spark.table('orders')\n"
        "df.write.saveAsTable('mart.x')\n"
    )
    ir = parse_pyspark(str(f))
    fqns = {t.fully_qualified_name.lower() for df in ir.dataframes for t in df.reads_from}
    assert "default.orders" in fqns


def test_spark_sql_inside_pyspark_extracts_tables(pyspark_fixture):
    """spark.sql("SELECT ... FROM prod.dim.customers ...") must reach sqlglot."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("08_spark_sql_inside.py")))

    all_sources = {t.fully_qualified_name.lower() for df in ir.dataframes for t in df.reads_from}
    assert "prod.dim.customers" in all_sources


def test_jdbc_source_captured(pyspark_fixture):
    """JDBC reads in the realistic fixture become :Table nodes with scheme=jdbc."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))

    jdbc_tables = [
        t for df in ir.dataframes for t in df.reads_from
        if t.storage_format == "jdbc" or (t.location or "").startswith("jdbc:")
    ]
    assert len(jdbc_tables) >= 1


def test_temp_view_resolved_in_subsequent_sql(pyspark_fixture):
    """`createOrReplaceTempView('raw_orders')` followed by `spark.sql(... FROM raw_orders)`
    must resolve raw_orders back to the original DataFrame's source."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("08_spark_sql_inside.py")))

    fqns_or_locs = []
    for df in ir.dataframes:
        for t in df.reads_from:
            fqns_or_locs.append((t.fully_qualified_name or "").lower())
            fqns_or_locs.append((t.location or "").lower())
    # The temp view points back to s3://raw/orders/
    assert any("s3://raw/orders" in x for x in fqns_or_locs)


def test_multiple_formats_in_one_script(pyspark_fixture):
    """The realistic fixture reads parquet, delta, JDBC — all should appear."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))

    formats = {t.storage_format for df in ir.dataframes for t in df.reads_from}
    assert {"parquet", "delta", "jdbc"} <= formats
