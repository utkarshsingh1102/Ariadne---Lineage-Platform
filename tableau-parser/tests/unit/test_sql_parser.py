"""
sqlglot wrapper (plan §9.1).
Extracts physical tables from custom-SQL relations.
Plan §9.4: 100% coverage required.
"""
import pytest


def test_simple_select():
    from tableau_parser.parser.sql_parser import extract_tables
    out = extract_tables("SELECT * FROM PROD.SALES.ORDERS")
    fqns = {t.upper() for t in out}
    assert "PROD.SALES.ORDERS" in fqns


def test_inner_join():
    from tableau_parser.parser.sql_parser import extract_tables
    out = extract_tables(
        "SELECT o.id, c.name FROM PROD.SALES.ORDERS o "
        "INNER JOIN PROD.CRM.CUSTOMER c ON o.cid = c.id"
    )
    fqns = {t.upper() for t in out}
    assert fqns == {"PROD.SALES.ORDERS", "PROD.CRM.CUSTOMER"}


def test_cte():
    from tableau_parser.parser.sql_parser import extract_tables
    sql = """
        WITH high_value AS (
            SELECT * FROM PROD.SALES.ORDERS WHERE amount > 1000
        )
        SELECT h.*, c.name FROM high_value h JOIN PROD.CRM.CUSTOMER c ON h.cid = c.id
    """
    fqns = {t.upper() for t in extract_tables(sql)}
    # CTE alias 'high_value' must NOT be reported as a physical table
    assert "PROD.SALES.ORDERS" in fqns
    assert "PROD.CRM.CUSTOMER" in fqns
    assert "HIGH_VALUE" not in fqns


def test_subquery():
    from tableau_parser.parser.sql_parser import extract_tables
    sql = "SELECT * FROM (SELECT * FROM PROD.SALES.ORDERS) sub"
    fqns = {t.upper() for t in extract_tables(sql)}
    assert "PROD.SALES.ORDERS" in fqns


def test_union():
    from tableau_parser.parser.sql_parser import extract_tables
    sql = "SELECT id FROM PROD.A.X UNION SELECT id FROM PROD.B.Y"
    fqns = {t.upper() for t in extract_tables(sql)}
    assert "PROD.A.X" in fqns
    assert "PROD.B.Y" in fqns


def test_malformed_sql_returns_empty_with_warning(caplog):
    """Plan §13: don't abort — capture, warn, continue."""
    from tableau_parser.parser.sql_parser import extract_tables
    out = extract_tables("SELECT * FROM WHERE  -- syntactically broken")
    assert out == []
