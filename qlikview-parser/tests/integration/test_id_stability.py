"""Phase 1 CI gate — determinism of v0.2 node ids.

Parsing the same input twice must produce identical sets of `node_id`
values across all v0.2 entity lists. This catches accidental
nondeterminism (Python set iteration leaking into qname construction,
timestamps in keys, etc.) at PR-merge time rather than in production.
"""
from __future__ import annotations

from qlikview_parser.ids import sha256_id


def _all_ids(app) -> set[str]:
    """Collect every v0.2 entity id from a parsed app — platforms,
    connections, sources, datasets, attributes, key constraints, plus
    lineage edge endpoints."""
    out: set[str] = set()
    for p in app.platforms:
        out.add(sha256_id(p.qname))
    for c in app.data_connections:
        out.add(sha256_id(c.qname))
    for s in app.physical_sources:
        out.add(sha256_id(s.qname))
    for d in app.datasets:
        out.add(sha256_id(d.qname))
    for a in app.attributes:
        out.add(sha256_id(a.qname))
    for k in app.key_constraints:
        out.add(sha256_id(k.qname))
    return out


def test_v2_ids_are_deterministic_across_runs(parse):
    """The same fixture parsed twice produces an identical set of ids."""
    fixtures = [
        "01_simple_sql_load.qvs",
        "02_resident_load.qvs",
        "03_left_join.qvs",
        "08_realistic_dashboard.qvs",
    ]
    for fname in fixtures:
        ids_a = _all_ids(parse(fname))
        ids_b = _all_ids(parse(fname))
        assert ids_a == ids_b, (
            f"id set diverged across runs for {fname!r}: "
            f"only in A={sorted(ids_a - ids_b)}, only in B={sorted(ids_b - ids_a)}"
        )


def test_v2_ids_are_full_sha256(parse):
    """v0.2 ids are 64 hex chars (full SHA-256, not the legacy 16-char
    truncation). Catches a regression to truncated ids at scale where
    birthday-collision risk becomes real."""
    app = parse("08_realistic_dashboard.qvs")
    for entity_id in _all_ids(app):
        assert len(entity_id) == 64, (
            f"v0.2 id is {len(entity_id)} chars, expected 64 — "
            f"someone re-introduced truncation"
        )
        # And the chars are hex
        int(entity_id, 16)


def test_v2_ids_are_lowercase_hex(parse):
    """SHA-256 hex output is conventionally lowercase. Catches case-flip
    regressions that would silently double the graph (MERGE keys are
    case-sensitive)."""
    app = parse("08_realistic_dashboard.qvs")
    for entity_id in _all_ids(app):
        assert entity_id == entity_id.lower()
