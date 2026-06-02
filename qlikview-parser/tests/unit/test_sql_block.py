"""
Embedded SQL SELECT extraction (plan §2.2 / §6 step 5).
Uses sqlglot to lift physical tables and columns out of the SQL body.
"""
import pytest


def test_sql_query_captured(parse):
    app = parse("01_simple_sql_load.qvs")
    assert app.loads[0].sql_query is not None
    assert "PROD.SALES.CUSTOMER" in app.loads[0].sql_query.upper()


def test_physical_table_lifted_from_sql(parse):
    app = parse("01_simple_sql_load.qvs")
    assert app.loads[0].source_table is not None
    # Plan §5.4: source_table should resolve to PROD.SALES.CUSTOMER (FQN).
    # Current regex captures only the bare name. Accept either for now.
    assert "CUSTOMER" in (app.loads[0].source_table or "").upper()


def test_physical_table_uses_fully_qualified_name(parse):
    app = parse("01_simple_sql_load.qvs")
    assert (app.loads[0].source_table or "").upper() == "PROD.SALES.CUSTOMER"


# -----------------------------------------------------------------------------
# Keyword/short-token blacklist (current regex prototype)
# -----------------------------------------------------------------------------

def test_sqlglot_blacklist_excludes_keywords(parser_no_neo4j):
    tables = parser_no_neo4j.extract_sql_tables(
        "SELECT a, b FROM PROD.SALES.ORDERS WHERE a > 0"
    )
    assert "ORDERS" in [t.upper() for t in tables]
    # Should NOT contain "SELECT", "FROM", "WHERE"
    for kw in ("SELECT", "FROM", "WHERE"):
        assert kw not in [t.upper() for t in tables]


def test_sqlglot_handles_join(parser_no_neo4j):
    sql = """
        SELECT o.OrderID, c.CustomerName
        FROM PROD.SALES.ORDERS o
        INNER JOIN PROD.SALES.CUSTOMER c ON o.CustomerID = c.CustomerID
    """
    tables = [t.upper() for t in parser_no_neo4j.extract_sql_tables(sql)]
    assert "ORDERS" in tables
    assert "CUSTOMER" in tables


# -----------------------------------------------------------------------------
# Vendor-specific dialects
# -----------------------------------------------------------------------------

def test_teradata_qualify_clause_parses(parse):
    app = parse("08_realistic_dashboard.qvs")
    orders = next(l for l in app.loads if l.table_name == "Orders")
    assert orders.source_table is not None  # Should resolve PROD.SALES.ORDERS


# -----------------------------------------------------------------------------
# Macro substitution must happen BEFORE sqlglot sees the SQL
# -----------------------------------------------------------------------------

def test_macro_substituted_before_sql_parse(parse):
    app = parse("06_variables_and_includes.qvs")
    yo = next(l for l in app.loads if l.table_name == "YearlyOrders")
    # $(vSchema) → PROD.SALES, so source_table must be the FULL FQN
    assert (yo.source_table or "").upper() == "PROD.SALES.ORDERS"
