"""Deterministic ID generation (plan §5.4 / §15).

All IDs are ``sha256(canonical_string)[:16]``. Canonical strings are lowercased
and built from a fixed schema so the same logical entity always hashes the same,
regardless of input casing or which parser produced it. This is the contract
that lets the Spark parser MERGE onto :Table nodes written by Tableau, QlikView,
TWS, etc.
"""
from __future__ import annotations

import hashlib

from .path_parser import canonical_path_id


def _hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def script_id(file_path: str) -> str:
    return _hash(f"spark_script::{file_path}")


def _canonical_table_string(database: str, schema: str, name: str) -> str:
    return f"table::{database}.{schema}.{name}".lower()


def table_id_hive(*, database: str, schema: str, name: str) -> str:
    return _hash(_canonical_table_string(database, schema, name))


def table_id_path(uri: str) -> str:
    return _hash(canonical_path_id(uri))


def dataframe_id(*, script_id: str, var_name: str, creation_order: int) -> str:
    return _hash(f"dataframe::{script_id}::{var_name}::{creation_order}")


def attribute_id_physical(*, table_fqn: str, column: str) -> str:
    return _hash(f"attribute::{table_fqn}::{column}".lower())


def attribute_id_in_memory(*, dataframe_id: str, column: str) -> str:
    return _hash(f"attribute::{dataframe_id}::{column}")


def udf_id(*, script_id: str, udf_name: str) -> str:
    return _hash(f"udf::{script_id}::{udf_name}")


def connection_id(
    *,
    klass: str,
    server: str,
    dbname: str,
    port: int | None = None,
) -> str:
    """Deterministic id for a :Connection node.

    Canonical form: ``connection::klass::server::port::dbname`` (all
    lowercased, ``port`` omitted when ``None``). When the caller has
    already filled in the default port for the dialect, ``host`` and
    ``host:default_port`` collapse to one node. Without the caller
    providing a port the legacy three-part shape is preserved so existing
    callers (and the Tableau parser) don't see a hash change.
    """
    parts: list[str] = ["connection", klass, server, dbname]
    if port is not None:
        # Insert port between server and dbname — fixed shape so dedup is
        # order-stable.
        parts = ["connection", klass, server, str(port), dbname]
    canonical = "::".join((s or "").strip().lower() for s in parts)
    return _hash(canonical)
