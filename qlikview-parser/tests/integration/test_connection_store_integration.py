"""Phase 3 — connection-store integration: an out-of-script LIB CONNECT
name resolves to a fully-populated ``DataConnection`` via the store."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def store_dir(tmp_path):
    """A connection store laid out with one .dconn and one Settings.ini
    section so we can exercise both resolution tiers."""
    d = tmp_path / "connections"
    d.mkdir()
    (d / "snowflake-prod.dconn").write_text(
        "<Connection>"
        "<Name>snowflake-prod</Name>"
        "<Type>OLEDB</Type>"
        "<ConnectionString>"
        "Driver=Snowflake;Server=acme.us-east-1.snowflakecomputing.com;"
        "Warehouse=ETL_WH;Database=PROD;Schema=CORE;Role=ETL_ROLE;"
        "UID=etl_user;PWD=hunter2hunter2"
        "</ConnectionString>"
        "</Connection>"
    )
    settings = tmp_path / "Settings.ini"
    settings.write_text(
        "[oracle-warehouse]\n"
        "Driver=Oracle ODBC\n"
        "Server=oracle-prod.internal\n"
        "Database=ORA12C\n"
    )
    return {"dconn_dir": str(d), "settings_ini": str(settings)}


def test_lib_connect_resolves_via_store(parser_no_neo4j, store_dir, tmp_path):
    """A bare ``LIB CONNECT TO 'snowflake-prod';`` resolves to a fully-
    populated DataConnection (host / database / warehouse / role / auth)
    via the configured .dconn store."""
    from qlikview_parser.core import QlikViewParser

    parser = QlikViewParser(
        neo4j_uri="m", neo4j_user="m", neo4j_password="m",
        **store_dir,
    )
    parser.driver = parser_no_neo4j.driver

    qvs = tmp_path / "app.qvs"
    qvs.write_text(
        "LIB CONNECT TO 'snowflake-prod';\n"
        "Customers:\n"
        "SQL SELECT id, name FROM CORE.CUSTOMERS;\n"
    )
    app = parser.parse_qvs_file(str(qvs))

    snowflake = [c for c in app.data_connections if c.name == "snowflake-prod"]
    assert snowflake, f"expected snowflake-prod DataConnection, got: {[c.name for c in app.data_connections]}"
    c = snowflake[0]
    assert c.host and "snowflakecomputing.com" in c.host
    assert c.database == "PROD"
    assert c.warehouse == "ETL_WH"
    assert c.role == "ETL_ROLE"
    assert c.auth_method == "password"
    # The plaintext password must never reach the IR.
    assert "hunter2hunter2" not in (c.raw_locator_redacted or "")


def test_unresolved_lib_name_falls_back_to_visitor_classification(
    parser_no_neo4j, store_dir, tmp_path,
):
    """A LIB name that isn't in any store keeps whatever the visitor
    classified inline (so we don't regress the pre-Phase-3 behavior)."""
    from qlikview_parser.core import QlikViewParser

    parser = QlikViewParser(
        neo4j_uri="m", neo4j_user="m", neo4j_password="m",
        **store_dir,
    )
    parser.driver = parser_no_neo4j.driver

    qvs = tmp_path / "mystery.qvs"
    qvs.write_text("LIB CONNECT TO 'not-in-any-store';\n")
    app = parser.parse_qvs_file(str(qvs))

    bare = [c for c in app.data_connections if c.name == "not-in-any-store"]
    assert bare, "bare LIB connection should still appear in app.data_connections"
    # Platform falls back to 'unknown' since the name carries no hints.
    assert bare[0].platform_kind == "unknown"


def test_store_diagnostics_propagate_to_app(parser_no_neo4j, tmp_path):
    """A malformed .dconn in the store emits ``QV-DCONN-PARSE`` and the
    diagnostic propagates onto the parsed app's diagnostics list."""
    from qlikview_parser.core import QlikViewParser

    bad_store = tmp_path / "connections"
    bad_store.mkdir()
    (bad_store / "broken.dconn").write_text("<<<not xml>>>")

    parser = QlikViewParser(
        neo4j_uri="m", neo4j_user="m", neo4j_password="m",
        dconn_dir=str(bad_store),
    )
    parser.driver = parser_no_neo4j.driver

    qvs = tmp_path / "app.qvs"
    qvs.write_text("LIB CONNECT TO 'broken';\n")
    app = parser.parse_qvs_file(str(qvs))

    codes = [d.code for d in app.diagnostics]
    assert "QV-DCONN-PARSE" in codes
