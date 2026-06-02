"""
End-to-end parse (no Neo4j).
Every fixture across all three input formats round-trips through the parser
without raising.
"""
import pytest


PYSPARK_FIXTURES = [
    "01_simple_read_write.py",
    "02_join_and_select.py",
    "03_with_column_chain.py",
    "04_groupby_agg.py",
    "05_union.py",
    "06_udf_usage.py",
    "07_dynamic_table_name.py",
    "08_spark_sql_inside.py",
    "09_realistic_etl.py",
]

SPARKSQL_FIXTURES = [
    "01_simple_ctas.sql",
    "02_insert_overwrite.sql",
    "03_merge_into.sql",
    "04_cte_chain.sql",
    "05_partition_write.sql",
]

NOTEBOOK_FIXTURES = [
    "01_simple.ipynb",
    "02_databricks_format.py",
    "03_mixed_python_sql.ipynb",
]


@pytest.mark.parametrize("name", PYSPARK_FIXTURES)
def test_pyspark_fixture_parses(pyspark_fixture, name):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture(name)))
    assert ir is not None
    assert ir.id and len(ir.id) == 16
    assert ir.script_type == "pyspark"


@pytest.mark.parametrize("name", SPARKSQL_FIXTURES)
def test_sparksql_fixture_parses(sparksql_fixture, name, read_text):
    from spark_parser.sparksql.lineage import extract_lineage
    lin = extract_lineage(read_text(sparksql_fixture(name)), dialect="spark")
    assert lin is not None
    assert lin.target_tables  # every fixture writes somewhere


@pytest.mark.parametrize("name", NOTEBOOK_FIXTURES)
def test_notebook_fixture_parses(notebook_fixture, name):
    """The orchestrator should route notebooks through the right backend."""
    from spark_parser.main import parse_input  # top-level dispatcher
    ir = parse_input(str(notebook_fixture(name)))
    assert ir is not None


def test_realistic_etl_stats(pyspark_fixture):
    """Plan §7 example response shape — adjust thresholds as the parser matures."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))

    src_tables = {(t.fully_qualified_name or t.location) for df in ir.dataframes for t in df.reads_from}
    tgt_tables = {(t.fully_qualified_name or t.location) for df in ir.dataframes for t in df.writes_to}

    assert len(src_tables) >= 4   # parquet + delta + jdbc + archive + customers
    assert len(tgt_tables) >= 3   # 3 write targets in the fixture
    assert len(ir.udfs) >= 1


@pytest.mark.slow
def test_realistic_under_5s(pyspark_fixture):
    """Plan §16: 500-line PySpark script must parse in <5s."""
    import time
    from spark_parser.pyspark.visitor import parse_pyspark
    start = time.time()
    parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))
    assert time.time() - start < 5


# -----------------------------------------------------------------------------
# Scope boundaries (plan §2.4) — assert the parser declines what it should
# -----------------------------------------------------------------------------

def test_scala_file_emits_warning_does_not_raise(tmp_path):
    """Plan §2.4 + §14: Scala is out of scope for v0.1.
    The parser must NOT crash — it should return an empty IR + warning."""
    from spark_parser.main import parse_input
    sc = tmp_path / "Main.scala"
    sc.write_text("object Main { def main(args: Array[String]): Unit = {} }")
    ir = parse_input(str(sc))
    assert ir is not None
    assert ir.warnings
    assert any("scala" in (w.detail or "").lower() for w in ir.warnings)
