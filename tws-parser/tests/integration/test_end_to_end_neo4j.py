"""
End-to-end Neo4j integration (plan §10.5, §12.1).
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


def test_minimal_schedule_writes_expected_nodes(clean_db, fixture_path):
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.graph.writer import GraphWriter

    schedules = parse_composer_text(str(fixture_path("01_single_schedule_single_job.txt")))
    GraphWriter(clean_db).write_schedules(schedules)

    with clean_db.session() as s:
        assert _label(s, "Schedule") == 1
        assert _label(s, "Job") == 1
        assert _label(s, "Script") == 1
        assert _rel(s, "CONTAINS_JOB") == 1
        assert _rel(s, "CALLS_SCRIPT") == 1


def test_realistic_dump_relationships(clean_db, fixture_path):
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.graph.writer import GraphWriter

    schedules = parse_composer_text(str(fixture_path("06_realistic_dump_many_schedules.txt")))
    GraphWriter(clean_db).write_schedules(schedules)

    with clean_db.session() as s:
        assert _label(s, "Schedule") == 3
        # Lots of FOLLOWS chains within sales pipeline + recon
        assert _rel(s, "DEPENDS_ON") >= 5
        # File-watcher dependency on the sales feed
        assert _rel(s, "WAITS_FOR_FILE") >= 1
        # Multiple NEEDS_RESOURCE edges
        assert _rel(s, "NEEDS_RESOURCE") >= 2


def test_validation_query_lineage_from_job_to_script(clean_db, fixture_path):
    """Plan §12.1: job → script lineage query returns expected rows."""
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.graph.writer import GraphWriter

    GraphWriter(clean_db).write_schedules(
        parse_composer_text(str(fixture_path("02_multi_job_with_follows.txt")))
    )

    with clean_db.session() as s:
        rows = s.run("""
            MATCH (j:Job {name:'LOAD_ORDERS_TO_DW'})-[:CALLS_SCRIPT]->(sc:Script)
            RETURN sc.path AS p, sc.script_type AS t
        """).single()
        assert rows is not None
        assert rows["p"].endswith("load_orders.bteq")
        assert rows["t"] == "bteq"


def test_uniqueness_constraints_present(clean_db):
    """Plan §5.3."""
    from tws_parser.graph.writer import GraphWriter
    GraphWriter(clean_db).ensure_constraints()
    with clean_db.session() as s:
        rows = list(s.run("SHOW CONSTRAINTS"))
        names = " ".join((r["name"] or "").lower() for r in rows)
        for must in ("schedule", "job", "script"):
            assert must in names


def test_reparse_idempotent(clean_db, fixture_path):
    """Plan §16: re-running produces zero diff."""
    from tws_parser.parser.composer import parse_composer_text
    from tws_parser.graph.writer import GraphWriter

    path = str(fixture_path("02_multi_job_with_follows.txt"))
    GraphWriter(clean_db).write_schedules(parse_composer_text(path))
    with clean_db.session() as s:
        n0 = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        r0 = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    GraphWriter(clean_db).write_schedules(parse_composer_text(path))
    with clean_db.session() as s:
        n1 = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        r1 = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    assert (n0, r0) == (n1, r1)
