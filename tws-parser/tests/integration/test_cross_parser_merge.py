"""
Cross-parser merge contract (plan §5.1 + §16 DoD).

If the Ab Initio parser already wrote :Script {path:'/apps/abinitio/run.sh'}
the TWS parser must MERGE onto that node — not create a duplicate.

This is the critical join point that connects scheduling lineage to data
lineage: TWS knows which scripts run when; the other parsers know what
those scripts read/write.
"""
import pytest

pytestmark = pytest.mark.neo4j


def test_no_duplicate_script_after_tws_parse(neo4j_env, fixture_path):
    from neo4j import GraphDatabase
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.graph.writer import GraphWriter

    drv = GraphDatabase.driver(neo4j_env["uri"], auth=(neo4j_env["user"], neo4j_env["password"]))
    try:
        with drv.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
            # Pre-seed: Ab Initio parser wrote this Script node first, lower-cased path
            s.run("""
                CREATE (sc:Script {
                    id: 'preseed_abinitio_run',
                    path: '/apps/abinitio/run.sh',
                    script_type: 'shell'
                })
                CREATE (g:AbInitioGraph {name: 'extract.mp'})-[:DEFINED_IN]->(sc)
            """)
            seeded = s.run(
                "MATCH (sc:Script {path:'/apps/abinitio/run.sh'}) RETURN count(sc) AS c"
            ).single()["c"]
            assert seeded == 1

        # Now parse the TWS dump — it references /apps/abinitio/run.sh
        schedules = parse_composer_text(str(fixture_path("02_multi_job_with_follows.txt")))
        GraphWriter(drv).write_schedules(schedules)

        with drv.session() as s:
            # Still exactly one Script node for that path
            count = s.run(
                "MATCH (sc:Script {path:'/apps/abinitio/run.sh'}) RETURN count(sc) AS c"
            ).single()["c"]
            assert count == 1, f"Cross-parser merge failed: {count} copies"

            # Verify the TWS Job → existing AbInitio script linkage works through one node
            row = s.run("""
                MATCH (j:Job {name:'EXTRACT_ORDERS'})-[:CALLS_SCRIPT]->(sc:Script)
                      <-[:DEFINED_IN]-(g:AbInitioGraph)
                RETURN j.name AS j, sc.path AS p, g.name AS g
            """).single()
            assert row is not None
            assert row["g"] == "extract.mp"
    finally:
        with drv.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
        drv.close()
