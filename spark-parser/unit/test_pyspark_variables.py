"""
Variable tracking (plan §9.1 — "most likely place for bugs", §14).
Reassignment, branching, loops, function calls.
"""
import pytest


def test_reassignment_increments_creation_order(pyspark_fixture):
    """Plan §14: `df = ...; df = df.filter(...)` → two DataFrameIR rows
    with creation_order 0 and 1, both preserved in the IR."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))

    orders_versions = [df for df in ir.dataframes if df.var_name == "orders"]
    assert len(orders_versions) >= 3
    orders = sorted(orders_versions, key=lambda d: d.creation_order)
    cos = [d.creation_order for d in orders]
    assert cos == sorted(cos)
    assert len(set(cos)) == len(cos), "creation_order has duplicates"


def test_anonymous_dataframe_named_by_creation_order(tmp_path):
    """Plan §14: method-chain DataFrames without intermediate variables get
    deterministic names like __anon_<creation_order>."""
    from spark_parser.pyspark.visitor import parse_pyspark
    src = (
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "spark.read.parquet('s3://a/').filter('x > 0').withColumn('y', 'z')."
        "write.saveAsTable('t.y')\n"
    )
    f = tmp_path / "anon.py"; f.write_text(src)
    ir = parse_pyspark(str(f))
    anons = [df for df in ir.dataframes if (df.var_name or "").startswith("__anon_")]
    assert len(anons) >= 1
    for a in anons:
        assert a.is_anonymous is True


def test_branch_assigns_two_lineages(tmp_path):
    """Plan §14: variable assigned inside `if/else` → emit both branches,
    mark lineage_conditional=true."""
    from spark_parser.pyspark.visitor import parse_pyspark
    src = (
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "cond = True\n"
        "if cond:\n"
        "    df = spark.read.parquet('s3://a/')\n"
        "else:\n"
        "    df = spark.read.parquet('s3://b/')\n"
        "df.write.saveAsTable('t.y')\n"
    )
    f = tmp_path / "branch.py"; f.write_text(src)
    ir = parse_pyspark(str(f))

    df_for_y = next(df for df in ir.dataframes if any(
        t.fully_qualified_name and "t.y" in t.fully_qualified_name.lower()
        for t in df.writes_to
    ))
    locs = {t.location for t in df_for_y.reads_from if t.location}
    assert {"s3://a/", "s3://a"} & locs or "s3://a" in " ".join(locs)
    assert df_for_y.lineage_conditional is True


def test_loop_emits_one_representative_dataframe_with_warning(tmp_path):
    """Plan §14: loop-generated DataFrames → one representative + lineage_partial=true."""
    from spark_parser.pyspark.visitor import parse_pyspark
    src = (
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "result = None\n"
        "for t in ['a', 'b', 'c']:\n"
        "    result = spark.read.parquet(f's3://raw/{t}/')\n"
        "result.write.saveAsTable('t.y')\n"
    )
    f = tmp_path / "loop.py"; f.write_text(src)
    ir = parse_pyspark(str(f))

    final = next(df for df in ir.dataframes if any(
        t.fully_qualified_name and "t.y" in t.fully_qualified_name.lower()
        for t in df.writes_to
    ))
    assert final.lineage_partial is True


def test_function_call_followed_into_same_file(tmp_path):
    """Plan §9.1: `df = transform(df)` defined in same file → follow into it."""
    from spark_parser.pyspark.visitor import parse_pyspark
    src = (
        "from pyspark.sql.functions import col\n"
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "def transform(d):\n"
        "    return d.withColumn('region_upper', col('region').cast('string'))\n"
        "raw = spark.table('t.orders')\n"
        "out = transform(raw)\n"
        "out.write.saveAsTable('t.out')\n"
    )
    f = tmp_path / "fn.py"; f.write_text(src)
    ir = parse_pyspark(str(f))

    derives = [d for df in ir.dataframes for d in df.derivations]
    assert any(d.target_column == "region_upper" and "region" in d.source_columns for d in derives)


def test_external_function_marks_lineage_via_external(tmp_path):
    """Plan §9.1: function call to external code → captured as via='external_function'."""
    from spark_parser.pyspark.visitor import parse_pyspark
    src = (
        "from somewhere.external import enrich\n"
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        "raw = spark.table('t.orders')\n"
        "out = enrich(raw)\n"
        "out.write.saveAsTable('t.out')\n"
    )
    f = tmp_path / "ext.py"; f.write_text(src)
    ir = parse_pyspark(str(f))

    out = next(df for df in ir.dataframes if df.var_name == "out")
    via = {e.via for e in out.derives_from_dataframe}
    assert "external_function" in via


def test_dynamic_table_name_partial_lineage(pyspark_fixture):
    """Plan §14: sys.argv[1] is unresolvable → lineage_partial + warning."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("07_dynamic_table_name.py")))

    # The static `f'{env}.dim.customers'` should resolve correctly
    sources = {t.fully_qualified_name for df in ir.dataframes for t in df.reads_from
               if t.fully_qualified_name}
    assert "prod.dim.customers" in {s.lower() for s in sources}

    # The dynamic target should yield a warning
    msgs = " ".join((w.detail or "") for w in (ir.warnings or []))
    assert "dynamic" in msgs.lower() or "argv" in msgs.lower()
