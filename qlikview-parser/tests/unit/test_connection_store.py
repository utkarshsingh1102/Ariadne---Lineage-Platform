"""Phase 3 — connection-store readers.

Verify that:
1. .dconn XML parses correctly (and tolerates UTF-16 BOM bytes).
2. odbc.ini / Settings.ini sections lift into ``DataConnection`` records.
3. Secrets in the resolved connection string are scrubbed before reaching
   ``raw_locator_redacted`` and a fingerprint is emitted for change-detect.
4. Lookup is case-insensitive on the LIB name.
5. Missing / corrupt stores degrade silently to ``resolve() -> None``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from qlikview_parser.connections import (
    ConnectionStore,
    parse_dconn_file,
    parse_ini_store,
)
from qlikview_parser.secrets import REDACTED


# ---- .dconn parsing --------------------------------------------------------

def test_parse_dconn_extracts_name_type_and_raw(tmp_path):
    p = tmp_path / "snowflake-prod.dconn"
    p.write_text(
        "<Connection>"
        "<Name>snowflake-prod</Name>"
        "<Type>OLEDB</Type>"
        "<ConnectionString>"
        "Driver=Snowflake;Server=acme.us-east-1.snowflakecomputing.com;"
        "Warehouse=ETL_WH;Database=PROD;Schema=CORE;UID=etl;PWD=hunter2"
        "</ConnectionString>"
        "</Connection>"
    )
    parsed = parse_dconn_file(p)
    assert parsed is not None
    assert parsed["name"] == "snowflake-prod"
    assert parsed["type"] == "OLEDB"
    assert "PWD=hunter2" in parsed["raw"]   # NOT scrubbed at parse time


def test_parse_dconn_tolerates_utf16_bom(tmp_path):
    p = tmp_path / "weird.dconn"
    xml = "<Connection><Name>x</Name><Type>ODBC</Type><ConnectionString>DSN=foo</ConnectionString></Connection>"
    p.write_bytes(b"\xff\xfe" + xml.encode("utf-16-le"))
    parsed = parse_dconn_file(p)
    assert parsed is not None
    assert parsed["name"] == "x"


def test_parse_dconn_missing_file_returns_none(tmp_path):
    assert parse_dconn_file(tmp_path / "nope.dconn") is None


def test_parse_dconn_malformed_returns_none(tmp_path):
    p = tmp_path / "broken.dconn"
    p.write_text("<<<not xml>>>")
    assert parse_dconn_file(p) is None


# ---- INI parsing -----------------------------------------------------------

def test_parse_ini_lowercases_keys(tmp_path):
    p = tmp_path / "odbc.ini"
    p.write_text(
        "[redshift_prod]\n"
        "Driver=Amazon Redshift\n"
        "Server=cluster.abc.us-east-1.redshift.amazonaws.com\n"
        "Database=analytics\n"
        "UID=etl\n"
        "PWD=secret\n"
    )
    parsed = parse_ini_store(p)
    assert "redshift_prod" in parsed
    body = parsed["redshift_prod"]
    assert body["driver"] == "Amazon Redshift"
    assert body["server"].startswith("cluster.abc")
    assert body["pwd"] == "secret"


def test_parse_ini_missing_returns_none(tmp_path):
    assert parse_ini_store(tmp_path / "nope.ini") is None


# ---- ConnectionStore resolver ----------------------------------------------

def test_resolver_returns_none_for_unknown(tmp_path):
    store = ConnectionStore.from_paths(
        dconn_dir=tmp_path, settings_ini=None, odbc_ini=None,
    )
    assert store.resolve("not-there") is None


def test_resolver_lifts_dconn_into_data_connection(tmp_path):
    d = tmp_path / "connections"
    d.mkdir()
    (d / "snowflake-prod.dconn").write_text(
        "<Connection>"
        "<Name>snowflake-prod</Name>"
        "<Type>OLEDB</Type>"
        "<ConnectionString>"
        "Driver=Snowflake;Server=acme.us-east-1.snowflakecomputing.com;"
        "Warehouse=ETL_WH;Database=PROD;Schema=CORE;Role=ETL_ROLE;UID=etl;PWD=hunter2"
        "</ConnectionString>"
        "</Connection>"
    )
    store = ConnectionStore.from_paths(dconn_dir=d, secret_salt=b"S")
    dc = store.resolve("snowflake-prod")
    assert dc is not None
    assert dc.name == "snowflake-prod"
    assert dc.platform_kind == "snowflake"
    assert dc.host and "snowflakecomputing.com" in dc.host
    assert dc.database == "PROD"
    assert dc.warehouse == "ETL_WH"
    assert dc.schema == "CORE"
    assert dc.role == "ETL_ROLE"
    assert dc.auth_method == "password"


def test_resolver_scrubs_secret_and_emits_fingerprint(tmp_path):
    d = tmp_path / "connections"
    d.mkdir()
    (d / "x.dconn").write_text(
        "<Connection><Name>x</Name><Type>OLEDB</Type>"
        "<ConnectionString>Driver=Snowflake;Server=acme;PWD=hunter2hunter2</ConnectionString>"
        "</Connection>"
    )
    store = ConnectionStore.from_paths(dconn_dir=d, secret_salt=b"deterministic-salt")
    dc = store.resolve("x")
    # The scrubbed locator must NOT contain the plaintext.
    assert "hunter2hunter2" not in dc.raw_locator_redacted
    assert REDACTED in dc.raw_locator_redacted
    # Fingerprint is deterministic on (salt, secret).
    assert dc.secret_fingerprint and len(dc.secret_fingerprint) == 32


def test_resolver_is_case_insensitive_on_name(tmp_path):
    d = tmp_path / "connections"
    d.mkdir()
    (d / "Snowflake-Prod.dconn").write_text(
        "<Connection><Name>Snowflake-Prod</Name><Type>OLEDB</Type>"
        "<ConnectionString>Driver=Snowflake;Server=acme</ConnectionString>"
        "</Connection>"
    )
    store = ConnectionStore.from_paths(dconn_dir=d)
    assert store.resolve("snowflake-prod") is not None
    assert store.resolve("SNOWFLAKE-PROD") is not None


def test_resolver_falls_through_to_settings_ini(tmp_path):
    settings = tmp_path / "Settings.ini"
    settings.write_text(
        "[redshift_etl]\n"
        "Driver=Amazon Redshift\n"
        "Server=cluster.abc.us-east-1.redshift.amazonaws.com\n"
        "Database=analytics\n"
        "PWD=topsecret\n"
    )
    store = ConnectionStore.from_paths(settings_ini=settings)
    dc = store.resolve("redshift_etl")
    assert dc is not None
    assert dc.platform_kind == "redshift"
    assert dc.host and "redshift.amazonaws.com" in dc.host
    assert "topsecret" not in dc.raw_locator_redacted
    assert dc.auth_method == "password"


def test_resolver_falls_through_to_odbc_ini(tmp_path):
    odbc = tmp_path / "odbc.ini"
    odbc.write_text(
        "[OracleProd]\n"
        "Driver=Oracle ODBC\n"
        "Server=oracle-prod.internal\n"
        "Database=ORA12C\n"
    )
    store = ConnectionStore.from_paths(odbc_ini=odbc)
    dc = store.resolve("OracleProd")
    assert dc is not None
    assert dc.platform_kind == "oracle"
    assert dc.host == "oracle-prod.internal"
    assert dc.database == "ORA12C"


def test_resolver_dconn_takes_precedence_over_ini(tmp_path):
    """If a name appears in BOTH a .dconn file AND an INI store, the
    .dconn wins (it's the more authoritative source in real QV deploys)."""
    d = tmp_path / "connections"
    d.mkdir()
    (d / "shared.dconn").write_text(
        "<Connection><Name>shared</Name><Type>OLEDB</Type>"
        "<ConnectionString>Driver=Snowflake;Server=via-dconn</ConnectionString>"
        "</Connection>"
    )
    ini = tmp_path / "Settings.ini"
    ini.write_text("[shared]\nDriver=Oracle ODBC\nServer=via-ini\n")
    store = ConnectionStore.from_paths(dconn_dir=d, settings_ini=ini)
    dc = store.resolve("shared")
    assert dc.platform_kind == "snowflake"
    assert dc.host == "via-dconn"


def test_resolver_emits_diagnostic_on_malformed_dconn(tmp_path):
    d = tmp_path / "connections"
    d.mkdir()
    (d / "bad.dconn").write_text("<<<broken>>>")
    store = ConnectionStore.from_paths(dconn_dir=d)
    assert store.resolve("bad") is None
    codes = [diag.code for diag in store.diagnostics]
    assert "QV-DCONN-PARSE" in codes
