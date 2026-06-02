"""
Neo4j schema contract (plan §5).
Requires a running Neo4j — gated by @pytest.mark.neo4j.
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


def _label_count(session, label):
    return session.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]


def _rel_count(session, rel):
    return session.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c").single()["c"]


def test_workbook_node_created(clean_db, fixture_path):
    from tableau_parser.parser.workbook import parse_workbook
    from tableau_parser.graph.writer import GraphWriter

    ir = parse_workbook(str(fixture_path("01_simple_single_datasource.twb")))
    GraphWriter(clean_db).write_workbook(ir)

    with clean_db.session() as s:
        assert _label_count(s, "TableauWorkbook") == 1
        assert _label_count(s, "TableauDatasource") == 1
        assert _label_count(s, "Connection") == 1
        assert _label_count(s, "Table") == 1


def test_required_relationships(clean_db, fixture_path):
    from tableau_parser.parser.workbook import parse_workbook
    from tableau_parser.graph.writer import GraphWriter

    ir = parse_workbook(str(fixture_path("01_simple_single_datasource.twb")))
    GraphWriter(clean_db).write_workbook(ir)

    with clean_db.session() as s:
        assert _rel_count(s, "CONTAINS_DATASOURCE") == 1
        assert _rel_count(s, "CONNECTS_VIA") == 1
        assert _rel_count(s, "READS_TABLE") == 1
        assert _rel_count(s, "HAS_COLUMN") >= 1


def test_calculated_field_derives_from_edge(clean_db, fixture_path):
    """Plan §5.2: DERIVES_FROM edges carry the formula."""
    from tableau_parser.parser.workbook import parse_workbook
    from tableau_parser.graph.writer import GraphWriter

    ir = parse_workbook(str(fixture_path("02_calculated_fields.twb")))
    GraphWriter(clean_db).write_workbook(ir)

    with clean_db.session() as s:
        # AmountWithTax → DERIVES_FROM → Amount
        result = s.run(
            "MATCH (calc:Attribute {name:'AmountWithTax'})-[r:DERIVES_FROM]->(src:Attribute) "
            "RETURN src.name, r.formula"
        ).data()
        srcs = {r["src.name"] for r in result}
        assert "Amount" in srcs


def test_uniqueness_constraints_present(clean_db):
    """Plan §5.3: constraints created on first write."""
    from tableau_parser.graph.writer import GraphWriter
    GraphWriter(clean_db).ensure_constraints()
    with clean_db.session() as s:
        rows = list(s.run("SHOW CONSTRAINTS"))
        names = " ".join((r["name"] or "").lower() for r in rows)
        for must_have in ("tableau_workbook", "tableau_datasource", "connection", "table_fqn", "attribute"):
            assert must_have in names, f"Missing constraint mentioning '{must_have}'"


def test_reparse_idempotent(clean_db, fixture_path):
    """Plan §15: re-parsing same file produces zero diff."""
    from tableau_parser.parser.workbook import parse_workbook
    from tableau_parser.graph.writer import GraphWriter

    path = str(fixture_path("01_simple_single_datasource.twb"))
    GraphWriter(clean_db).write_workbook(parse_workbook(path))
    with clean_db.session() as s:
        nodes_a = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rels_a = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    GraphWriter(clean_db).write_workbook(parse_workbook(path))
    with clean_db.session() as s:
        nodes_b = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        rels_b = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    assert nodes_a == nodes_b
    assert rels_a == rels_b
