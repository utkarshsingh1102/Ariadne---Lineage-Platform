"""
Variable extraction tests (plan §2.7).
SET and LET statements should produce :Variable IR entries.
Macro substitution $(varName) must happen before SQL parsing (plan §6 step 5c).
Currently unimplemented — see REVIEW.md §3.3.
"""
import pytest


def test_set_variable_captured(parse):
    app = parse("06_variables_and_includes.qvs")
    variables = getattr(app, "variables", None) or []
    names = {v.name for v in variables}
    assert "vReportingYear" in names
    assert "vSchema" in names


def test_let_variable_captured(parse):
    app = parse("06_variables_and_includes.qvs")
    variables = getattr(app, "variables", None) or []
    names = {v.name for v in variables}
    assert "vToday" in names
    assert "vYearStart" in names


def test_variable_scope_recorded(parse):
    app = parse("06_variables_and_includes.qvs")
    variables = getattr(app, "variables", None) or []
    by_name = {v.name: v for v in variables}
    assert by_name["vReportingYear"].scope == "set"
    assert by_name["vToday"].scope == "let"


def test_macro_expanded_in_sql(parse):
    app = parse("06_variables_and_includes.qvs")
    yo = next(l for l in app.loads if l.table_name == "YearlyOrders")
    # After expansion: FROM PROD.SALES.ORDERS WHERE YEAR(...) = 2025
    assert "2025" in (yo.sql_query or "")
    assert "PROD.SALES.ORDERS" in (yo.sql_query or "").upper()
    assert "$(" not in (yo.sql_query or ""), "Unexpanded macro left in SQL"
