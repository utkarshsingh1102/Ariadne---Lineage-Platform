"""
Neo4j schema contract (plan §5).
Requires NEO4J_* env vars.
"""
import pytest

pytestmark = pytest.mark.neo4j


@pytest.fixture
def clean_db(neo4j_env):
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(neo4j_env["uri"], auth=(neo4j_env["user"], neo4j_env["password"]))
    with drv.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    yield drv
    with drv.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    drv.close()


def _label(s, lbl):
    return s.run(f"MATCH (n:{lbl}) RETURN count(n) AS c").single()["c"]


def _rel(s, t):
    return s.run(f"MATCH ()-[r:{t}]->() RETURN count(r) AS c").single()["c"]


def test_minimal_script_writes_expected_nodes(clean_db, pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    from spark_parser.graph.writer import GraphWriter

    ir = parse_pyspark(str(pyspark_fixture("01_simple_read_write.py")))
    GraphWriter(clean_db).write_script(ir)

    with clean_db.session() as s:
        assert _label(s, "SparkScript") == 1
        assert _label(s, "DataFrame") >= 1
        # One source path + one target Hive table
        assert _label(s, "Table") == 2
        assert _rel(s, "READS_TABLE") >= 1
        assert _rel(s, "WRITES_TABLE") >= 1


def test_derives_from_edge_carries_formula(clean_db, pyspark_fixture):
    """Plan §5.2: DERIVES_FROM has `formula` and `via` properties."""
    from spark_parser.pyspark.visitor import parse_pyspark
    from spark_parser.graph.writer import GraphWriter

    ir = parse_pyspark(str(pyspark_fixture("03_with_column_chain.py")))
    GraphWriter(clean_db).write_script(ir)

    with clean_db.session() as s:
        row = s.run("""
            MATCH (calc:Attribute {name:'region_upper'})-[r:DERIVES_FROM]->(src:Attribute)
            RETURN src.name AS s, r.formula AS f, r.via AS v
        """).single()
        assert row is not None
        assert row["s"] == "region"
        assert row["v"] in {"withColumn", "select"}


def test_joins_with_edge_written(clean_db, pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    from spark_parser.graph.writer import GraphWriter

    ir = parse_pyspark(str(pyspark_fixture("02_join_and_select.py")))
    GraphWriter(clean_db).write_script(ir)

    with clean_db.session() as s:
        assert _rel(s, "JOINS_WITH") >= 1


def test_uses_udf_edge_written(clean_db, pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    from spark_parser.graph.writer import GraphWriter

    ir = parse_pyspark(str(pyspark_fixture("06_udf_usage.py")))
    GraphWriter(clean_db).write_script(ir)

    with clean_db.session() as s:
        assert _label(s, "UDF") >= 2
        assert _rel(s, "USES_UDF") >= 2


def test_uniqueness_constraints_present(clean_db):
    """Plan §5.3."""
    from spark_parser.graph.writer import GraphWriter
    GraphWriter(clean_db).ensure_constraints()
    with clean_db.session() as s:
        rows = list(s.run("SHOW CONSTRAINTS"))
        names = " ".join((r["name"] or "").lower() for r in rows)
        for must in ("spark_script", "dataframe", "table_fqn", "attribute", "udf"):
            assert must in names


def test_reparse_idempotent(clean_db, pyspark_fixture):
    """Plan §15 + §16: re-running produces zero net diff."""
    from spark_parser.pyspark.visitor import parse_pyspark
    from spark_parser.graph.writer import GraphWriter

    path = str(pyspark_fixture("02_join_and_select.py"))
    GraphWriter(clean_db).write_script(parse_pyspark(path))
    with clean_db.session() as s:
        n0 = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        r0 = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    GraphWriter(clean_db).write_script(parse_pyspark(path))
    with clean_db.session() as s:
        n1 = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        r1 = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    assert (n0, r0) == (n1, r1)


def test_validation_query_target_tables(clean_db, pyspark_fixture):
    """Plan §11: WRITES_TABLE query returns the script's outputs."""
    from spark_parser.pyspark.visitor import parse_pyspark
    from spark_parser.graph.writer import GraphWriter

    ir = parse_pyspark(str(pyspark_fixture("09_realistic_etl.py")))
    GraphWriter(clean_db).write_script(ir)

    with clean_db.session() as s:
        rows = s.run("""
            MATCH (s:SparkScript {name:'09_realistic_etl'})
                  -[:CONTAINS_DATAFRAME]->(:DataFrame)
                  -[:WRITES_TABLE]->(t:Table)
            RETURN DISTINCT toLower(t.fully_qualified_name) AS fqn
        """).data()
        fqns = {r["fqn"] for r in rows if r["fqn"]}
        assert "prod.mart.orders_enriched" in fqns
        assert "prod.mart.summary_daily" in fqns
