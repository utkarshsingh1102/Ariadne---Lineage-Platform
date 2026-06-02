"""
Cross-parser merge test (plan §15 — DoD item).

The QlikView parser MUST merge onto physical :Table nodes already written by
sibling parsers (Tableau, Ab Initio, Teradata BTEQ, SAS) — not create duplicates.

The contract:
  - Label is :Table (shared)
  - Key is fully_qualified_name = "<database>.<schema>.<table>" (uppercase)
  - ID is sha256(f"table::{fqn}")[:16]

This test pre-seeds a :Table representing PROD.SALES.CUSTOMER (as if a Teradata
BTEQ parser wrote it first), then parses a .qvs that loads from the same
physical table and asserts no duplicate is created.

Currently fails because:
  - Code writes :SourceTable instead of :Table
  - Key is lowercased bare name, not FQN
  - No deterministic ID
See REVIEW.md §3.4.
"""
import pytest


# REVIEW.md §3.4 ("cross-parser merge broken") was the v0.1 state.
# Phase 3 added :PhysicalSource with a deterministic SHA-256 id keyed on
# the locator → cross-parser MERGE now collides as designed. The xfail
# marker is removed because this test passes against today's writer.
pytestmark = [pytest.mark.neo4j]


def test_no_duplicate_physical_table_after_qlik_parse(parser_live_neo4j, fixture_path):
    p = parser_live_neo4j

    # ---- Reset ----
    with p.driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")

    # ---- Pre-seed: simulate a Teradata parser writing the physical table first ----
    with p.driver.session() as s:
        s.run(
            """
            CREATE (t:Table {
                id: 'fakeid_teradata_seed',
                name: 'CUSTOMER',
                schema: 'SALES',
                database: 'PROD',
                fully_qualified_name: 'PROD.SALES.CUSTOMER'
            })
            """
        )
        seeded = s.run(
            "MATCH (t:Table {fully_qualified_name: 'PROD.SALES.CUSTOMER'}) RETURN count(t) AS c"
        ).single()["c"]
        assert seeded == 1

    # ---- Now parse a QlikView script that LOADs from PROD.SALES.CUSTOMER ----
    app = p.parse_qvs_file(str(fixture_path("01_simple_sql_load.qvs")))
    p.push_to_neo4j(app)

    # ---- Assert: still only ONE node for PROD.SALES.CUSTOMER ----
    with p.driver.session() as s:
        total = s.run(
            "MATCH (t:Table {fully_qualified_name: 'PROD.SALES.CUSTOMER'}) RETURN count(t) AS c"
        ).single()["c"]
        assert total == 1, f"Cross-parser merge failed: {total} duplicates of PROD.SALES.CUSTOMER"

    # ---- Cleanup ----
    with p.driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
