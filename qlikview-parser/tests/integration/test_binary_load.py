"""Phase 2 integration tests — BINARY load inheritance + cross-app stitching.

A QlikView app declaring ``BINARY 'upstream.qvw';`` at the top of its
script inherits the upstream app's entire data model. The orchestrator
follows the directive recursively (depth + cycle guarded) and merges
the upstream Datasets / Attributes / PhysicalSources into the current
app's IR, emitting ``DERIVES_FROM`` edges so the lineage view can walk
the chain.

Cross-app stitching is verified separately: two apps that both touch
the same QVD file produce a PhysicalSource node with identical SHA-256
id (the locator path is the same → same qname → same hash).
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def upstream_qvs(tmp_path):
    """Write an upstream .qvs that defines Customers + Orders."""
    p = tmp_path / "upstream.qvs"
    p.write_text(
        "LIB CONNECT TO 'snowflake-prod';\n"
        "Customers:\n"
        "SQL SELECT id, name FROM CORE.CUSTOMERS;\n"
        "STORE Customers INTO 'qvd/customers.qvd' (qvd);\n"
        "Orders:\n"
        "SQL SELECT id, customer_id, total FROM CORE.ORDERS;\n"
        "STORE Orders INTO 'qvd/orders.qvd' (qvd);\n"
    )
    return p


def test_binary_load_inherits_upstream_datasets(parser_no_neo4j, upstream_qvs, tmp_path):
    """A downstream app with ``BINARY 'upstream.qvs';`` should end up
    with the upstream's Customers + Orders datasets in its IR."""
    downstream = tmp_path / "downstream.qvs"
    downstream.write_text(
        f"BINARY '{upstream_qvs.name}';\n"
        "LOAD * INLINE [\n"
        "    Label\n"
        "    derived\n"
        "];\n"
    )

    app = parser_no_neo4j.parse_qvs_file(str(downstream))

    ds_names = {d.name for d in app.datasets}
    assert "CORE.CUSTOMERS" in ds_names or "Customers" in ds_names, (
        f"upstream Customers dataset missing — got: {ds_names}"
    )
    assert "CORE.ORDERS" in ds_names or "Orders" in ds_names, (
        f"upstream Orders dataset missing — got: {ds_names}"
    )

    # Inherited attributes — the leaf nodes from upstream's SQL columns.
    attr_names = {a.name.lower() for a in app.attributes}
    assert "id" in attr_names
    assert "name" in attr_names
    assert "customer_id" in attr_names
    assert "total" in attr_names


def test_binary_load_emits_derives_from_edges(parser_no_neo4j, upstream_qvs, tmp_path):
    """For every inherited Dataset, a ``DERIVES_FROM`` edge from the
    downstream script to the upstream Dataset is added so the lineage
    view can render the inheritance arrow."""
    downstream = tmp_path / "downstream.qvs"
    downstream.write_text(f"BINARY '{upstream_qvs.name}';\n")

    app = parser_no_neo4j.parse_qvs_file(str(downstream))

    derives = [e for e in app.lineage_edges if e.rel == "DERIVES_FROM"]
    assert derives, "no DERIVES_FROM edges emitted for BINARY inheritance"
    assert all(e.transform == "BINARY_LOAD" for e in derives)


def test_binary_load_inheritance_diagnostic(parser_no_neo4j, upstream_qvs, tmp_path):
    """An info diagnostic records what was inherited."""
    downstream = tmp_path / "downstream.qvs"
    downstream.write_text(f"BINARY '{upstream_qvs.name}';\n")

    app = parser_no_neo4j.parse_qvs_file(str(downstream))
    inherited = [d for d in app.diagnostics if d.code == "QV-BINARY-INHERITED"]
    assert len(inherited) == 1
    assert "upstream.qvs" in inherited[0].message


def test_missing_binary_target_is_soft_fail(parser_no_neo4j, tmp_path):
    """A BINARY pointing at a non-existent file produces a warn-level
    diagnostic, NOT an exception."""
    downstream = tmp_path / "downstream.qvs"
    downstream.write_text("BINARY 'nope.qvw';\n")

    app = parser_no_neo4j.parse_qvs_file(str(downstream))
    not_found = [d for d in app.diagnostics if d.code == "QV-BINARY-NOT-FOUND"]
    assert len(not_found) == 1


def test_binary_cycle_breaks_with_diagnostic(parser_no_neo4j, tmp_path):
    """A → B → A must NOT recurse infinitely — the cycle guard kicks in."""
    a = tmp_path / "a.qvs"
    b = tmp_path / "b.qvs"
    a.write_text("BINARY 'b.qvs';\nLOAD * INLINE [X\n1\n];\n")
    b.write_text("BINARY 'a.qvs';\nLOAD * INLINE [Y\n1\n];\n")

    app = parser_no_neo4j.parse_qvs_file(str(a))
    cycle = [d for d in app.diagnostics if d.code == "QV-BINARY-CYCLE"]
    assert len(cycle) >= 1, (
        f"cycle guard didn't fire — diagnostics: "
        f"{[(d.code, d.message[:40]) for d in app.diagnostics]}"
    )


def test_cross_app_qvd_stitching_via_deterministic_id(parser_no_neo4j, tmp_path):
    """Two apps that touch the same QVD path produce a PhysicalSource
    with IDENTICAL SHA-256 id — verifies the v2 plan's stitching
    contract holds without any extra plumbing."""
    from qlikview_parser.ids import sha256_id, physical_source_qname

    # App A — produces qvd/customers.qvd
    a_path = tmp_path / "a.qvs"
    a_path.write_text(
        "LIB CONNECT TO 'sf';\n"
        "Customers:\n"
        "SQL SELECT id FROM CORE.CUSTOMERS;\n"
        "STORE Customers INTO 'qvd/customers.qvd' (qvd);\n"
    )

    # App B — consumes the same qvd path (no BINARY; just a LOAD that
    # references it).
    b_path = tmp_path / "b.qvs"
    b_path.write_text(
        "LIB CONNECT TO 'sf';\n"
        "Customers:\n"
        "LOAD * FROM 'qvd/customers.qvd' (qvd);\n"
    )

    app_a = parser_no_neo4j.parse_qvs_file(str(a_path))

    parser_b = type(parser_no_neo4j)(
        neo4j_uri="m", neo4j_user="m", neo4j_password="m"
    )
    parser_b.driver = parser_no_neo4j.driver  # share mock
    app_b = parser_b.parse_qvs_file(str(b_path))

    a_qvd = [s for s in app_a.physical_sources if s.kind == "qvd"]
    b_qvd = [s for s in app_b.physical_sources if s.kind == "qvd"]
    # Phase 3.5 closed the LOAD-FROM-QVD emission gap (the visitor now
    # emits a :PhysicalSource for every ``LOAD ... FROM '<file>' (qvd)``
    # in addition to STORE INTO), so both sides MUST produce a qvd
    # PhysicalSource. A miss here is a real regression.
    assert a_qvd, "App A (STORE INTO qvd) didn't emit a PhysicalSource"
    assert b_qvd, "App B (LOAD FROM qvd) didn't emit a PhysicalSource"

    # The IDs must be IDENTICAL — that's the cross-parser stitching
    # contract the v2 plan guarantees.
    a_id = sha256_id(a_qvd[0].qname)
    b_id = sha256_id(b_qvd[0].qname)
    assert a_id == b_id, (
        f"PhysicalSource ids diverged: a={a_id}, b={b_id} "
        f"(qnames: a={a_qvd[0].qname!r} b={b_qvd[0].qname!r})"
    )


def test_stitching_helper_id_matches_for_same_locator():
    """Sanity check on the qname → id pipeline at the helper level —
    cross-parser stitching depends on this never drifting."""
    from qlikview_parser.ids import sha256_id, physical_source_qname

    q1 = physical_source_qname(None, "qvd/customers.qvd")
    q2 = physical_source_qname(None, "qvd/customers.qvd")
    assert q1 == q2
    assert sha256_id(q1) == sha256_id(q2)
    assert len(sha256_id(q1)) == 64
