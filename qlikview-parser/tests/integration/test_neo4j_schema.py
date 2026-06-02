"""
Neo4j schema integration tests (plan §5).
Requires a running Neo4j — gated by @pytest.mark.neo4j.

These assert the *plan's* schema, not the current code's. They will fail until
REVIEW.md §3.4 (label/relationship rename + deterministic IDs) is done.
"""
import pytest


pytestmark = pytest.mark.neo4j


@pytest.fixture
def clean_db(parser_live_neo4j):
    """Wipe the database before each test."""
    with parser_live_neo4j.driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    yield parser_live_neo4j
    with parser_live_neo4j.driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")


def _label_count(session, label):
    return session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]


def _rel_count(session, rel_type):
    return session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS c").single()["c"]


# -----------------------------------------------------------------------------
# Plan §5.1 — node labels
# -----------------------------------------------------------------------------

def test_qlikscript_node_created(clean_db, fixture_path):
    app = clean_db.parse_qvs_file(str(fixture_path("01_simple_sql_load.qvs")))
    clean_db.push_to_neo4j(app)
    with clean_db.driver.session() as s:
        assert _label_count(s, "QlikScript") == 1


def test_connection_node_created(clean_db, fixture_path):
    app = clean_db.parse_qvs_file(str(fixture_path("01_simple_sql_load.qvs")))
    clean_db.push_to_neo4j(app)
    with clean_db.driver.session() as s:
        assert _label_count(s, "Connection") == 1


def test_physical_table_label_is_Table(clean_db, fixture_path):
    app = clean_db.parse_qvs_file(str(fixture_path("01_simple_sql_load.qvs")))
    clean_db.push_to_neo4j(app)
    with clean_db.driver.session() as s:
        assert _label_count(s, "Table") >= 1


# -----------------------------------------------------------------------------
# Plan §5.2 — relationship types
# -----------------------------------------------------------------------------

def test_uses_connection_edge(clean_db, fixture_path):
    app = clean_db.parse_qvs_file(str(fixture_path("01_simple_sql_load.qvs")))
    clean_db.push_to_neo4j(app)
    with clean_db.driver.session() as s:
        assert _rel_count(s, "USES_CONNECTION") >= 1


def test_loads_from_table_edge(clean_db, fixture_path):
    app = clean_db.parse_qvs_file(str(fixture_path("01_simple_sql_load.qvs")))
    clean_db.push_to_neo4j(app)
    with clean_db.driver.session() as s:
        assert _rel_count(s, "LOADS_FROM_TABLE") >= 1


def test_joins_with_edge(clean_db, fixture_path):
    app = clean_db.parse_qvs_file(str(fixture_path("03_left_join.qvs")))
    clean_db.push_to_neo4j(app)
    with clean_db.driver.session() as s:
        assert _rel_count(s, "JOINS_WITH") >= 1


# -----------------------------------------------------------------------------
# Plan §5.3 — uniqueness constraints
# -----------------------------------------------------------------------------

def test_uniqueness_constraints_exist(clean_db, fixture_path):
    app = clean_db.parse_qvs_file(str(fixture_path("01_simple_sql_load.qvs")))
    clean_db.push_to_neo4j(app)
    with clean_db.driver.session() as s:
        names = [r["name"] for r in s.run("SHOW CONSTRAINTS")]
        assert any("qlik_script" in n.lower() for n in names)
        assert any("connection" in n.lower() for n in names)
        assert any("table_fqn" in n.lower() for n in names)


# -----------------------------------------------------------------------------
# Plan §15 — idempotency
# -----------------------------------------------------------------------------

def test_reparsing_is_idempotent(clean_db, fixture_path):
    path = str(fixture_path("01_simple_sql_load.qvs"))
    clean_db.push_to_neo4j(clean_db.parse_qvs_file(path))

    with clean_db.driver.session() as s:
        nodes_before = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rels_before = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]

    clean_db.push_to_neo4j(clean_db.parse_qvs_file(path))

    with clean_db.driver.session() as s:
        nodes_after = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rels_after = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]

    assert nodes_before == nodes_after, f"Nodes: {nodes_before} → {nodes_after}"
    assert rels_before == rels_after, f"Rels:  {rels_before} → {rels_after}"
