"""
Cross-parser merge contract (plan §5.1 + §15 DoD).

If the Ab Initio / Teradata parser wrote :Table {fully_qualified_name:
'PROD.SALES.ORDERS'} first, the Tableau parser must MERGE onto that node
when it encounters the same physical table — no duplicates.
"""
import pytest

pytestmark = pytest.mark.neo4j


def test_no_duplicate_table_after_tableau_parse(neo4j_env, fixture_path):
    from neo4j import GraphDatabase
    from tableau_parser.parser.workbook import parse_workbook
    from tableau_parser.graph.writer import GraphWriter

    drv = GraphDatabase.driver(neo4j_env["uri"], auth=(neo4j_env["user"], neo4j_env["password"]))
    try:
        with drv.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
            # Pre-seed: an Ab Initio parser wrote PROD.SALES.CUSTOMER first
            s.run("""
                CREATE (t:Table {
                    id: 'preseed_abinitio',
                    name: 'CUSTOMER',
                    schema: 'SALES',
                    database: 'PROD',
                    fully_qualified_name: 'PROD.SALES.CUSTOMER'
                })
            """)
            seeded = s.run(
                "MATCH (t:Table {fully_qualified_name:'PROD.SALES.CUSTOMER'}) RETURN count(t) AS c"
            ).single()["c"]
            assert seeded == 1

        # Parse a Tableau workbook that loads PROD.SALES.Customer
        ir = parse_workbook(str(fixture_path("01_simple_single_datasource.twb")))
        GraphWriter(drv).write_workbook(ir)

        with drv.session() as s:
            count = s.run(
                "MATCH (t:Table {fully_qualified_name:'PROD.SALES.CUSTOMER'}) RETURN count(t) AS c"
            ).single()["c"]
            assert count == 1, f"Cross-parser merge failed: {count} copies"
            # The Tableau write must have added the HAS_COLUMN edge to the *same* node
            cols = s.run(
                "MATCH (t:Table {fully_qualified_name:'PROD.SALES.CUSTOMER'})-[:HAS_COLUMN]->(a:Attribute) "
                "RETURN count(a) AS c"
            ).single()["c"]
            assert cols >= 1
    finally:
        with drv.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
        drv.close()
