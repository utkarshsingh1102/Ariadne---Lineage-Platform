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


# ---------------------------------------------------------------------------
# Regression: spark.createDataFrame
# Without recognising it the LHS variable never binds, so downstream
# df.join() / df.write...  on that variable silently vanished. Discovered by
# running 97 spark-examples files; pre-fix dropped every join in every file
# that used createDataFrame.
# ---------------------------------------------------------------------------

def test_create_dataframe_binds_variable_so_downstream_joins_count(tmp_path):
    from spark_parser.pyspark.visitor import parse_pyspark
    f = tmp_path / "create_df.py"
    f.write_text(
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "emp = [(1, 'Smith', 10), (2, 'Rose', 20)]\n"
        "empCols = ['emp_id', 'name', 'dept_id']\n"
        "empDF = spark.createDataFrame(data=emp, schema=empCols)\n"
        "dept = [('Finance', 10), ('Sales', 20)]\n"
        "deptCols = ['dept_name', 'dept_id']\n"
        "deptDF = spark.createDataFrame(data=dept, schema=deptCols)\n"
        "empDF.join(deptDF, empDF.dept_id == deptDF.dept_id, 'left').show()\n"
        "empDF.join(deptDF, empDF.dept_id == deptDF.dept_id, 'inner').show()\n"
    )
    ir = parse_pyspark(str(f))
    join_count = sum(len(df.joins) for df in ir.dataframes)
    assert join_count == 2, f"expected 2 joins, got {join_count}"


def test_create_dataframe_extracts_columns_from_schema_list(tmp_path):
    """Schema as a list-literal of strings (the common pattern) should yield
    AttributeIR fields on the DataFrame."""
    from spark_parser.pyspark.visitor import parse_pyspark
    f = tmp_path / "create_df_schema.py"
    f.write_text(
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "data = [(1, 'a'), (2, 'b')]\n"
        "df = spark.createDataFrame(data=data, schema=['id', 'label'])\n"
    )
    ir = parse_pyspark(str(f))
    matching = [d for d in ir.dataframes if d.var_name == "df"]
    assert matching, "df not bound — createDataFrame was not recognised"
    field_names = [f.name for f in matching[0].fields]
    assert field_names == ["id", "label"]


def test_create_dataframe_no_upstream_source(tmp_path):
    """In-memory data: createDataFrame must NOT create a phantom source
    table (data is a Python literal, not a file/db read)."""
    from spark_parser.pyspark.visitor import parse_pyspark
    f = tmp_path / "create_df_no_src.py"
    f.write_text(
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "df = spark.createDataFrame([(1,)], ['n'])\n"
    )
    ir = parse_pyspark(str(f))
    matching = [d for d in ir.dataframes if d.var_name == "df"]
    assert matching
    assert matching[0].reads_from == [], "createDataFrame should not produce a source table"
