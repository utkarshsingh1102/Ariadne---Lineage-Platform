"""
Connection extraction tests (plan §2.1 / §5).
Asserts ODBC, OLEDB, and LIB CONNECT TO statements are captured as
:Connection nodes with class / server / dbname populated.
"""
import pytest


# -----------------------------------------------------------------------------
# ODBC
# -----------------------------------------------------------------------------

def test_odbc_connection_captured(parse):
    app = parse("01_simple_sql_load.qvs")
    odbc = [c for c in app.connections if c.type.value == "ODBC"]
    assert len(odbc) == 1
    assert odbc[0].name == "TERADATA_PROD"


def test_odbc_data_source_populated(parse):
    app = parse("01_simple_sql_load.qvs")
    assert app.connections[0].data_source == "TERADATA_PROD"


# -----------------------------------------------------------------------------
# OLEDB
# -----------------------------------------------------------------------------

def test_oledb_connection_in_realistic_fixture(parse):
    app = parse("08_realistic_dashboard.qvs")
    oledb = [c for c in app.connections if c.type.value == "OLEDB"]
    assert len(oledb) >= 1
    assert "mssql-prod" in (oledb[0].data_source or "")


# -----------------------------------------------------------------------------
# LIB CONNECT TO (Qlik Sense managed connections) — plan §2.1
# -----------------------------------------------------------------------------

def test_lib_connect_to_captured(parse):
    app = parse("08_realistic_dashboard.qvs")
    lib = [c for c in app.connections if c.type.value == "LIB"]
    assert len(lib) >= 1
    assert lib[0].name == "TeradataProd"


# -----------------------------------------------------------------------------
# Cross-include connection inheritance
# -----------------------------------------------------------------------------

def test_connections_from_included_file_propagate(parse):
    app = parse("08_realistic_dashboard.qvs")
    names = [c.name for c in app.connections]
    # Specifically the LIB connection, which is ONLY defined in the included file
    assert "TeradataProd" in names
    # And TERADATA_PROD must appear exactly once (deduped across main + include)
    assert names.count("TERADATA_PROD") == 1


# -----------------------------------------------------------------------------
# No spurious connections in scripts that have none
# -----------------------------------------------------------------------------

def test_no_connections_when_script_has_none(parse):
    app = parse("05_file_load.qvs")
    assert app.connections == []
