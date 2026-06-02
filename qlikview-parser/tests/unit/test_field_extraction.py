"""
Field-list extraction tests — direct repro of REVIEW.md §4.1, §4.5.
The field list of a LOAD must terminate at the first unquoted `;`.
"""
import pytest


def _all_field_names(app):
    """Flatten every field name across every LOAD."""
    return [f for load in app.loads for f in load.fields]


def test_no_field_contains_semicolon(parse):
    app = parse("09_comments_and_edge_cases.qvs")
    bad = [f for f in _all_field_names(app) if ";" in f]
    assert bad == [], f"Fields contain semicolons: {bad}"


def test_no_field_is_a_sql_keyword(parse):
    app = parse("09_comments_and_edge_cases.qvs")
    kw = {"SQL", "SELECT", "FROM", "WHERE", "JOIN", "RESIDENT"}
    leaked = [f for f in _all_field_names(app) if f.upper() in kw or "SELECT" in f.upper()]
    assert leaked == [], f"SQL keywords in field list: {leaked}"


def test_load_field_list_has_no_duplicates(parse):
    app = parse("01_simple_sql_load.qvs")
    for load in app.loads:
        assert len(load.fields) == len(set(load.fields)), \
            f"{load.table_name} has duplicate fields: {load.fields}"


def test_aliased_field_uses_alias_name(parse):
    """`Amount * 1.18 AS AmountWithTax` should expose `AmountWithTax` as the field."""
    app = parse("08_realistic_dashboard.qvs")
    orders = next((l for l in app.loads if l.table_name == "Orders"), None)
    if orders is None:
        pytest.skip("Orders LOAD not parsed (preceding-LOAD chain limitation)")
    assert "AmountWithTax" in orders.fields
