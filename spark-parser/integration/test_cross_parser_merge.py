"""
Cross-parser merge contract (plan §5.1 + §11 + §16 DoD).

Two scenarios:

  1. Pre-seed a :Table {fully_qualified_name:'prod.dim.customers'} as if the
     Tableau or Teradata parser wrote it first. Parse a PySpark script that
     reads from prod.dim.customers. Assert no duplicate :Table.

  2. Cross-parser end-to-end (the "whole point of the project"): create a
     fake :TableauDashboard → :TableauWorksheet → :Attribute chain, then
     parse a Spark script that WRITES the table the dashboard reads from,
     and assert the lineage query from plan §11 returns a row.
"""
import pytest

pytestmark = pytest.mark.neo4j


def test_no_duplicate_table_after_spark_parse(neo4j_env, pyspark_fixture):
    from neo4j import GraphDatabase
    from spark_parser.pyspark.visitor import parse_pyspark
    from spark_parser.graph.writer import GraphWriter

    drv = GraphDatabase.driver(neo4j_env["uri"], auth=(neo4j_env["user"], neo4j_env["password"]))
    try:
        with drv.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
            s.run("""
                CREATE (t:Table {
                    id: 'preseed_other_parser',
                    name: 'customers',
                    schema: 'dim',
                    database: 'prod',
                    fully_qualified_name: 'prod.dim.customers'
                })
            """)
            assert s.run(
                "MATCH (t:Table {fully_qualified_name:'prod.dim.customers'}) RETURN count(t) AS c"
            ).single()["c"] == 1

        ir = parse_pyspark(str(pyspark_fixture("02_join_and_select.py")))
        GraphWriter(drv).write_script(ir)

        with drv.session() as s:
            count = s.run(
                "MATCH (t:Table {fully_qualified_name:'prod.dim.customers'}) RETURN count(t) AS c"
            ).single()["c"]
            assert count == 1, f"Cross-parser merge failed: {count} copies"
    finally:
        with drv.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
        drv.close()


def test_tableau_to_spark_lineage_query(neo4j_env, pyspark_fixture):
    """Plan §11 — the whole-point query: Tableau dashboard → Table → Spark script."""
    from neo4j import GraphDatabase
    from spark_parser.pyspark.visitor import parse_pyspark
    from spark_parser.graph.writer import GraphWriter

    drv = GraphDatabase.driver(neo4j_env["uri"], auth=(neo4j_env["user"], neo4j_env["password"]))
    try:
        with drv.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
            # A fake Tableau lineage that reads from prod.mart.orders_enriched
            s.run("""
                CREATE (d:TableauDashboard {id:'d1', name:'Sales Overview'})
                CREATE (w:TableauWorksheet {id:'w1', name:'Monthly Sales'})
                CREATE (a:Attribute {id:'a1', name:'amount'})
                CREATE (t:Table {
                    id:'t1', name:'orders_enriched', schema:'mart', database:'prod',
                    fully_qualified_name:'prod.mart.orders_enriched'
                })
                CREATE (d)-[:DISPLAYS_WORKSHEET]->(w)
                CREATE (w)-[:USES_FIELD]->(a)
                CREATE (t)-[:HAS_COLUMN]->(a)
            """)

        # Now parse a Spark script that WRITES prod.mart.orders_enriched
        ir = parse_pyspark(str(pyspark_fixture("02_join_and_select.py")))
        GraphWriter(drv).write_script(ir)

        # Run the lineage query verbatim from plan §11
        with drv.session() as s:
            rows = s.run("""
                MATCH (dashboard:TableauDashboard)
                      -[:DISPLAYS_WORKSHEET]->(:TableauWorksheet)
                      -[:USES_FIELD]->(:Attribute)
                      <-[:HAS_COLUMN]-(t:Table)
                      <-[:WRITES_TABLE]-(:DataFrame)
                      <-[:CONTAINS_DATAFRAME]-(spark:SparkScript)
                RETURN dashboard.name AS d, t.fully_qualified_name AS t, spark.name AS s
            """).data()
            assert len(rows) >= 1, "Cross-parser lineage query returned no rows"
            assert rows[0]["d"] == "Sales Overview"
            assert rows[0]["t"].lower() == "prod.mart.orders_enriched"
    finally:
        with drv.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
        drv.close()
