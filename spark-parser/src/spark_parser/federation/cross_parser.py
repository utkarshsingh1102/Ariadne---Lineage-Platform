"""Cross-parser federation helpers — v0.2 §10.

The lineage knowledge graph is the union of every parser's output. Tables that
multiple parsers reference must share a single ``:Table`` node, keyed on the
deterministic FQN that every parser produces from the same canonical rules.

This module exposes pure helpers that:

  - Compute the canonical table id for a table FQN (mirrors
    ``utils.ids.table_id_hive`` but exposed for cross-parser assertion code).
  - Compare two parsers' table sets and report FQNs that should merge.

It does **not** touch Neo4j directly — that lives in ``graph/writer.py``.
This module is what the cross-parser integration tests import.
"""
from __future__ import annotations

from typing import Iterable

from ..utils.ids import table_id_hive, table_id_path


def canonical_table_id(fqn: str | None, *, location: str | None = None) -> str | None:
    """Return the canonical sha256 id every parser writes for ``fqn``."""
    if fqn:
        parts = fqn.split(".")
        if len(parts) == 3:
            db, schema, name = parts
        elif len(parts) == 2:
            db, schema, name = "", parts[0], parts[1]
        else:
            db, schema, name = "", "", fqn
        return table_id_hive(database=db or "", schema=schema or "", name=name)
    if location:
        return table_id_path(location)
    return None


def shared_table_ids(
    *,
    spark_fqns: Iterable[str],
    other_fqns: Iterable[str],
) -> set[str]:
    """Return the set of canonical ids referenced by BOTH parsers.

    Useful for asserting "the dashboard read column ``revenue`` from the same
    table the Spark job wrote it to" without touching Neo4j.
    """
    spark_ids = {
        canonical_table_id(fqn) for fqn in spark_fqns if fqn
    }
    other_ids = {
        canonical_table_id(fqn) for fqn in other_fqns if fqn
    }
    return {x for x in (spark_ids & other_ids) if x is not None}
