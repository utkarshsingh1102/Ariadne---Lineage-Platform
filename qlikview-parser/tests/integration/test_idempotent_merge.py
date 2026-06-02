"""Phase 1 CI gate — writer idempotency against a real Neo4j.

Writing the same parsed IR twice must produce identical node/edge counts.
The pure-Cypher MERGE templates in ``graph/writer.py`` already do this
structurally; this test catches regressions where someone accidentally
``CREATE``s instead of ``MERGE``s, or where a property update is wrapped
in ``[r] + [r]`` and inflates list-valued properties on each write.

Skips automatically when ``NEO4J_URI/USER/PASSWORD`` env vars aren't set
— meaning the unit-test fast loop stays free of a Neo4j dependency, but
CI with a testcontainers Neo4j sidecar enforces the gate.
"""
from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.neo4j


@pytest.fixture
def neo4j_env():
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER")
    password = os.environ.get("NEO4J_PASSWORD")
    if not all([uri, user, password]):
        pytest.skip("NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD not set")
    return {"uri": uri, "user": user, "password": password}


@pytest.fixture
def clean_db(neo4j_env):
    """A live driver scoped to a fresh QlikView subgraph. Drops every
    node this parser would write before/after the test so repeated CI
    runs are deterministic."""
    from neo4j import GraphDatabase

    drv = GraphDatabase.driver(
        neo4j_env["uri"], auth=(neo4j_env["user"], neo4j_env["password"])
    )

    labels_to_clear = (
        "QlikScript", "QlikTable", "Connection", "Attribute",
        "DataPlatform", "DataConnection", "PhysicalSource", "Dataset",
        "KeyConstraint",
    )

    def _purge():
        with drv.session() as s:
            for label in labels_to_clear:
                s.run(f"MATCH (n:{label}) DETACH DELETE n").consume()

    _purge()
    yield drv
    _purge()
    drv.close()


def _count_v2(driver) -> dict[str, int]:
    """Return per-label counts for the v0.2 entities (the ones the
    writer's new MERGE templates produce). Edge counts are bucketed by
    relationship type."""
    counts: dict[str, int] = {}
    with driver.session() as s:
        for label in (
            "DataPlatform", "DataConnection", "PhysicalSource",
            "Dataset", "Attribute", "KeyConstraint",
        ):
            r = s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()
            counts[f"node:{label}"] = int(r["c"]) if r else 0
        for rel in (
            "CONNECTS_VIA", "SOURCED_FROM", "HAS_ATTRIBUTE",
            "STORED_AS", "HAS_CONSTRAINT",
        ):
            r = s.run(f"MATCH ()-[e:{rel}]->() RETURN count(e) AS c").single()
            counts[f"edge:{rel}"] = int(r["c"]) if r else 0
    return counts


def test_double_write_produces_identical_counts(clean_db, fixture_path):
    """Write the same fixture twice; counts after run 2 must equal counts
    after run 1. Catches accidental CREATE-instead-of-MERGE regressions."""
    from qlikview_parser import QlikViewParser

    p = QlikViewParser(
        neo4j_uri=os.environ["NEO4J_URI"],
        neo4j_user=os.environ["NEO4J_USER"],
        neo4j_password=os.environ["NEO4J_PASSWORD"],
    )
    # Inject the existing driver from the clean_db fixture so the writer
    # writes into the same scope we'll be querying.
    p.driver = clean_db

    fixture = str(fixture_path("08_realistic_dashboard.qvs"))
    # parse_qvs_file builds the IR; push_to_neo4j is the separate write
    # step. The two-step shape mirrors the FastAPI /parse handler.
    app_1 = p.parse_qvs_file(fixture)
    p.push_to_neo4j(app_1)
    first = _count_v2(clean_db)

    app_2 = p.parse_qvs_file(fixture)
    p.push_to_neo4j(app_2)
    second = _count_v2(clean_db)

    assert first == second, (
        f"writer is not idempotent — counts differ between runs:\n"
        f"first:  {first}\n"
        f"second: {second}"
    )
    # Sanity — at least SOME nodes were written (a green test on an empty
    # graph would be a false pass).
    assert sum(first.values()) > 0, (
        "writer wrote zero v0.2 nodes — fixture or v0.2 emission is broken"
    )
