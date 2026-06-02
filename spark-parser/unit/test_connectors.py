"""Unit tests for v0.2 §9 — external-ecosystem connectors."""
from __future__ import annotations

from pathlib import Path

from spark_parser.connectors import match_connector
from spark_parser.pyspark.visitor import parse_pyspark


# ---------------------------------------------------------------------------
# match_connector — direct dispatch tests
# ---------------------------------------------------------------------------

def test_kafka_match():
    cm = match_connector(
        "kafka",
        {"kafka.bootstrap.servers": "broker:9092", "subscribe": "orders"},
    )
    assert cm is not None
    assert cm.storage_format == "kafka"
    assert cm.fully_qualified_name == "kafka://broker:9092/orders"


def test_iceberg_match_with_table_arg():
    cm = match_connector("iceberg", {}, table_arg="hive_prod.db.events")
    assert cm is not None
    assert cm.storage_format == "iceberg"
    assert cm.fully_qualified_name == "hive_prod.db.events"


def test_hudi_match_path():
    cm = match_connector("hudi", {}, path_arg="s3://datalake/hudi/users")
    assert cm is not None
    assert cm.storage_format == "hudi"
    assert cm.location == "s3://datalake/hudi/users"


def test_snowflake_match_three_part_fqn():
    cm = match_connector(
        "snowflake",
        {
            "sfUrl": "ab.snowflakecomputing.com",
            "sfDatabase": "PROD",
            "sfSchema": "DIM",
            "dbtable": "CUSTOMERS",
        },
    )
    assert cm is not None
    assert cm.storage_format == "snowflake"
    assert cm.fully_qualified_name == "PROD.DIM.CUSTOMERS"


def test_bigquery_match():
    cm = match_connector("bigquery", {"table": "proj.ds.t"})
    assert cm is not None
    assert cm.storage_format == "bigquery"
    assert cm.fully_qualified_name == "proj.ds.t"


def test_redshift_match():
    cm = match_connector(
        "redshift",
        {"url": "jdbc:redshift://rs.example.com:5439/prod", "dbtable": "public.events"},
    )
    assert cm is not None
    assert cm.storage_format == "redshift"
    assert cm.fully_qualified_name == "public.events"


def test_unknown_format_returns_none():
    assert match_connector("parquet", {}) is None
    assert match_connector(None, {}) is None
    assert match_connector("", {}) is None


# ---------------------------------------------------------------------------
# End-to-end through the visitor
# ---------------------------------------------------------------------------

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "pyspark" / "connectors" / "connector_pipeline.py"


def test_visitor_picks_up_all_connector_reads():
    ir = parse_pyspark(FIXTURE)
    fmts = {t.storage_format for df in ir.dataframes for t in df.reads_from}
    expected = {"kafka", "iceberg", "hudi", "snowflake", "bigquery", "redshift"}
    assert expected.issubset(fmts), f"missing: {expected - fmts}"


def test_visitor_emits_connector_writes_with_fqn():
    ir = parse_pyspark(FIXTURE)
    write_fmts = {t.storage_format for df in ir.dataframes for t in df.writes_to}
    # Three writes in the fixture: kafka, iceberg, snowflake.
    assert {"kafka", "iceberg", "snowflake"}.issubset(write_fmts)
    # Each write target should carry a usable FQN or location.
    for df in ir.dataframes:
        for t in df.writes_to:
            if t.storage_format in {"kafka", "iceberg", "snowflake"}:
                assert t.fully_qualified_name or t.location
