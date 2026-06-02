"""Deterministic node identity (v2 plan §0 + §3).

Every node in the lineage graph is keyed on
``id = sha256(qualified_name.encode("utf-8")).hexdigest()`` (full 64 hex
chars — not truncated). Two runs over identical input produce identical
ids; two parsers that derive the same qualified-name (e.g. a TWS job and
a QlikView script both pointing at ``/apps/etl/load_facts.sh``) MERGE
onto the same Neo4j node.

The truncation that the v0.1 writer used (16 chars) gave a birthday-
collision probability that's small but real at estate-scale (~2³² entities).
v0.2 uses the full digest — collisions are not a concern.
"""
from __future__ import annotations

import hashlib


def sha256_id(qname: str) -> str:
    """Full 64-hex-char SHA-256 of the qualified name."""
    return hashlib.sha256(qname.encode("utf-8")).hexdigest()


# Helpers per the qualified-name grammar in ``models.py``. Use these
# everywhere instead of building qnames by hand so the format stays
# centralised and the stitching contract with other parsers remains stable.

def platform_qname(kind: str, account_locator: str | None) -> str:
    return f"platform::{kind}:{account_locator or ''}"


def connection_qname(name: str) -> str:
    return f"conn::{name}"


def physical_source_qname(connection: str | None, locator: str) -> str:
    prefix = connection or "_local"
    return f"source::{prefix}/{locator}"


def dataset_qname(app: str, name: str) -> str:
    return f"dataset::{app}/table::{name}"


def attribute_qname(dataset_q: str, name: str) -> str:
    """``dataset_q`` is the already-built ``dataset::…`` qname."""
    return f"{dataset_q}/field::{name}"


def constraint_qname(dataset_q: str, kind: str, columns: tuple[str, ...]) -> str:
    return f"constraint::{dataset_q}/{kind}/{'+'.join(columns)}"
