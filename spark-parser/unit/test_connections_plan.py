"""Tests for the connection-extraction plan (``spark-improvement/connections.md``).

Two layers:

1. **Property-based invariants** — generic rules that must hold for EVERY
   fixture in ``fixtures/pyspark/connection_wide/``. They're the real
   coverage: adding a new fixture exercises every property automatically.
2. **Fixture-specific assertions** — one focused test per fixture for the
   exact behaviour that fixture is meant to pin down (sorted brokers,
   dict-config resolution, env-var node minted, …).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from spark_parser.connectors.registry import (
    LOCALHOST_ALIASES,
    URI_SCHEMES,
    canonical_host,
    is_credential_option_key,
    jdbc_default_port,
    lookup_format,
    lookup_scheme,
    normalize_path,
    sort_host_list,
)
from spark_parser.connectors import strip_url_credentials, split_credential_options
from spark_parser.pyspark.visitor import parse_pyspark
from spark_parser.graph.connection_queries import (
    ir_downstream_dataframes,
    ir_upstream_dataframes,
)


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pyspark" / "connection_wide"


def _iter_fixture_paths() -> list[Path]:
    return sorted(FIXTURES.glob("*.py"))


def _all_connections(ir):
    seen = []
    for df in ir.dataframes:
        for tbl in df.reads_from:
            if tbl.connection:
                seen.append(("read", df, tbl, tbl.connection))
        for edge in df.write_edges:
            if edge.target.connection:
                seen.append(("write", df, edge.target, edge.target.connection))
    return seen


# ---------------------------------------------------------------------------
# Registry self-checks — confirm the data-driven core is consistent.
# ---------------------------------------------------------------------------

def test_registry_scheme_lookup_round_trip():
    for entry in URI_SCHEMES:
        for s in entry.schemes:
            assert lookup_scheme(s) is entry, s


def test_registry_format_lookup_round_trip():
    # Picked a few that should always be present.
    assert lookup_format("jdbc") is not None
    assert lookup_format("kafka").default_port == 9092
    assert lookup_format("mongo").klass == "mongodb"
    assert lookup_format("UNKNOWNZZZ") is None


def test_default_port_jdbc():
    assert jdbc_default_port("postgresql") == 5432
    assert jdbc_default_port("mysql") == 3306
    assert jdbc_default_port("garbage") is None


def test_localhost_aliases_recognised():
    assert canonical_host("127.0.0.1") == "localhost"
    assert canonical_host("::1") == "localhost"
    assert canonical_host("LocalHost") == "localhost"
    for alias in LOCALHOST_ALIASES:
        assert canonical_host(alias) == "localhost"


def test_path_normalization_strips_trailing_slash():
    assert normalize_path("/gold/orders/") == "/gold/orders"
    assert normalize_path("/") == "/"


def test_sort_host_list_stable():
    assert sort_host_list(["b", "a", "c", "a"]) == ("a", "b", "c")


def test_credential_option_keys_caught():
    assert is_credential_option_key("password")
    assert is_credential_option_key("token")
    assert not is_credential_option_key("dbtable")


def test_strip_url_credentials_jdbc():
    masked, had = strip_url_credentials("jdbc:postgresql://app:secret@host:5432/db")
    assert "secret" not in masked
    assert masked.startswith("jdbc:postgresql://host")
    assert had


def test_strip_url_credentials_querystring():
    masked, had = strip_url_credentials(
        "jdbc:postgresql://h:5432/db?user=app&password=p&currentSchema=public",
    )
    assert "password=" not in masked
    assert "currentSchema" in masked
    assert had


def test_split_credential_options_drops_creds():
    safe, had = split_credential_options({"url": "X", "password": "Y", "token": "Z"})
    assert "password" not in safe and "token" not in safe
    assert safe.get("url") == "X"
    assert had


# ---------------------------------------------------------------------------
# Property-based invariants — must hold for every fixture.
# ---------------------------------------------------------------------------

ALL_FIXTURE_PATHS = _iter_fixture_paths()


@pytest.mark.parametrize("path", ALL_FIXTURE_PATHS, ids=lambda p: p.name)
def test_every_io_site_has_at_least_one_connection_edge(path):
    """Plan §8 property 1 — no orphan I/O sites."""
    ir = parse_pyspark(str(path))
    # At least one connection somewhere in the graph for any fixture that
    # actually performs I/O.
    seen = _all_connections(ir)
    assert seen, f"{path.name}: no Connection extracted from any I/O site"


@pytest.mark.parametrize("path", ALL_FIXTURE_PATHS, ids=lambda p: p.name)
def test_no_secret_values_appear_in_any_node(path):
    """Plan §8 property 4 — passwords / tokens never land in the graph."""
    ir = parse_pyspark(str(path))
    seen = _all_connections(ir)
    forbidden = ("should-never-appear", "leaked-if-not-stripped", "no-leak", "reddit-leak", "should-not-leak", "leak")
    for _, _, _tbl, conn in seen:
        haystack = " ".join(
            str(v) for v in [conn.server, conn.dbname, conn.schema, conn.username, *conn.options.values()]
        )
        for needle in forbidden:
            assert needle not in haystack, f"{path.name}: leaked '{needle}' into {haystack!r}"


@pytest.mark.parametrize("path", ALL_FIXTURE_PATHS, ids=lambda p: p.name)
def test_connections_dedup_by_canonical_id(path):
    """Plan §8 property 2 — N references to same DB → ≤1 distinct Connection
    id PER (klass, normalised host:port, dbname).
    """
    ir = parse_pyspark(str(path))
    seen = _all_connections(ir)
    grouped: dict[tuple, set[str]] = {}
    for _side, _df, _tbl, conn in seen:
        key = (conn.klass, conn.server, conn.port, conn.dbname)
        grouped.setdefault(key, set()).add(conn.id or "")
    for key, ids in grouped.items():
        assert len(ids) == 1, f"{path.name}: key {key} → multiple ids {ids}"


@pytest.mark.parametrize("path", ALL_FIXTURE_PATHS, ids=lambda p: p.name)
def test_unresolved_connections_still_have_edges(path):
    """Plan §8 property 5 — unresolved → node minted, edges intact."""
    ir = parse_pyspark(str(path))
    seen = _all_connections(ir)
    for _side, _df, _tbl, conn in seen:
        if not conn.resolved:
            # The id must still be deterministic and non-empty.
            assert conn.id, f"{path.name}: unresolved Connection has empty id"
            # And the source label tells reviewers *why* it's unresolved.
            assert conn.source, f"{path.name}: unresolved Connection has no source label"


# ---------------------------------------------------------------------------
# Per-fixture pinning tests.
# ---------------------------------------------------------------------------

def _parse(name: str):
    return parse_pyspark(str(FIXTURES / name))


def test_jdbc_postgres_options_splat_resolves_url_and_dbtable():
    ir = _parse("jdbc_postgres_options_splat.py")
    seen = _all_connections(ir)
    assert seen, "expected at least one Connection"
    conn = seen[0][3]
    assert conn.klass == "jdbc:postgresql"
    assert conn.server == "rds-prod.example.com"
    assert conn.port == 5432
    assert conn.dbname == "ecom"
    assert conn.has_credentials, "password key in splat must flag has_credentials"


def test_jdbc_mysql_literal_option_chain():
    ir = _parse("jdbc_mysql_literal_option.py")
    seen = _all_connections(ir)
    conn = seen[0][3]
    assert conn.klass == "jdbc:mysql"
    assert conn.port == 3306
    assert conn.dbname == "inventory"


def test_jdbc_three_arg_positional_resolves_table_and_writes():
    ir = _parse("jdbc_three_arg_positional.py")
    sides = sorted({s for s, *_ in _all_connections(ir)})
    assert sides == ["read", "write"], sides
    # Both edges point at the same Connection node.
    conns = {c.id for *_, c in _all_connections(ir)}
    assert len(conns) == 1, f"expected one Connection id, got {conns}"


def test_snowflake_sfurl_yields_snowflake_klass():
    ir = _parse("snowflake_sfurl.py")
    conn = _all_connections(ir)[0][3]
    assert conn.klass == "snowflake"
    assert conn.dbname == "PROD"


def test_kafka_readstream_chain_is_streaming():
    ir = _parse("kafka_readstream_chain.py")
    seen = _all_connections(ir)
    kafka = [c for *_, c in seen if c.klass == "kafka"]
    assert kafka, "no kafka connection"
    # Two brokers, sorted → only one Connection id whether broker order varies.
    assert len({c.id for c in kafka}) == 1


def test_s3_fstring_path_resolves_to_bucket_node():
    ir = _parse("s3_fstring_path.py")
    seen = _all_connections(ir)
    klasses = {c.klass for *_, c in seen}
    assert "s3" in klasses


def test_adls_abfss_container_in_dbname():
    ir = _parse("adls_abfss_path.py")
    conn = _all_connections(ir)[0][3]
    assert conn.klass == "adls"
    assert conn.dbname == "orders"


def test_gcs_path_yields_gcs_klass():
    ir = _parse("gcs_path.py")
    conn = _all_connections(ir)[0][3]
    assert conn.klass == "gcs"


def test_mongo_uri_strips_credentials_and_keeps_host():
    ir = _parse("mongo_uri.py")
    conn = _all_connections(ir)[0][3]
    assert conn.klass == "mongodb"
    assert "reddit-leak" not in str(conn.server or "")
    assert conn.has_credentials


def test_env_var_url_node_unresolved():
    ir = _parse("env_var_url.py")
    seen = _all_connections(ir)
    conn = seen[0][3]
    assert conn.resolved is False
    assert conn.source == "env"


def test_secret_dbutils_node_unresolved():
    ir = _parse("secret_dbutils.py")
    seen = _all_connections(ir)
    conn = seen[0][3]
    assert conn.resolved is False
    assert conn.source == "secret"


def test_reader_options_accumulated_across_statements():
    ir = _parse("reader_options_accumulated.py")
    seen = _all_connections(ir)
    conn = seen[0][3]
    assert conn.klass == "jdbc:postgresql"
    assert conn.server == "shared.example.com"
    assert conn.dbname == "etl"


def test_options_update_mutation_propagates():
    ir = _parse("options_update_mutation.py")
    seen = _all_connections(ir)
    conn = seen[0][3]
    assert conn.klass == "jdbc:postgresql"
    assert conn.server == "shared.example.com"


def test_dict_based_config_resolves():
    ir = _parse("dict_based_config.py")
    conn = _all_connections(ir)[0][3]
    assert conn.klass == "jdbc:postgresql"
    assert conn.server == "config-dict.example.com"


def test_two_config_classes_resolve_independently():
    ir = _parse("two_config_classes.py")
    seen = _all_connections(ir)
    klasses = {c.klass for *_, c in seen}
    assert {"jdbc:postgresql", "s3"}.issubset(klasses)


def test_localhost_127_dedup_to_single_node():
    ir = _parse("localhost_ip_normalization.py")
    conns = {c.id for *_, c in _all_connections(ir)}
    assert len(conns) == 1, f"expected single dedup'd connection, got {conns}"


def test_default_port_fill_dedup():
    ir = _parse("default_port_dedup.py")
    conns = {c.id for *_, c in _all_connections(ir)}
    assert len(conns) == 1, f"default-port fill should dedup, got {conns}"


def test_unknown_scheme_still_produces_connection():
    ir = _parse("unknown_scheme.py")
    seen = _all_connections(ir)
    conn = seen[0][3]
    assert conn.klass.startswith("unknown:"), conn.klass


# ---------------------------------------------------------------------------
# The original three (postgres bidirectional, kafka 2-broker, s3 multi-IO).
# ---------------------------------------------------------------------------

def test_postgres_bidirectional_one_node_with_both_edges():
    ir = _parse("postgres_bidirectional.py")
    seen = _all_connections(ir)
    sides = sorted({s for s, *_ in seen})
    ids = {c.id for *_, c in seen}
    assert sides == ["read", "write"]
    assert len(ids) == 1, ids


def test_kafka_two_broker_dedup_collapses_orderings():
    ir = _parse("kafka_two_broker_dedup.py")
    kafka = [c for *_, c in _all_connections(ir) if c.klass == "kafka"]
    assert kafka
    assert len({c.id for c in kafka}) == 1


def test_s3_multi_io_one_node():
    ir = _parse("s3_multi_io_one_node.py")
    seen = _all_connections(ir)
    s3_ids = {c.id for *_, c in seen if c.klass == "s3"}
    assert len(s3_ids) == 1, s3_ids


# ---------------------------------------------------------------------------
# Helper queries — Cypher templates compile, IR helpers return ids.
# ---------------------------------------------------------------------------

def test_ir_downstream_and_upstream_helpers():
    ir = _parse("postgres_bidirectional.py")
    seen = _all_connections(ir)
    cid = seen[0][3].id
    down = ir_downstream_dataframes(ir, cid)
    up = ir_upstream_dataframes(ir, cid)
    assert down, "expected ≥1 downstream DataFrame for the read connection"
    assert up, "expected ≥1 upstream DataFrame for the write connection"
