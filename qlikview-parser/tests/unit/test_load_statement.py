"""
LOAD statement tests (plan §2.2 / §6 step 5).
Each labelled LOAD becomes a :QlikTable with the right name, fields, and source_type.
"""
import pytest


# -----------------------------------------------------------------------------
# Simple SQL load
# -----------------------------------------------------------------------------

def test_simple_sql_load_table_name(parse):
    app = parse("01_simple_sql_load.qvs")
    assert len(app.loads) == 1
    assert app.loads[0].table_name == "Customer"


def test_simple_sql_load_source_type(parse):
    app = parse("01_simple_sql_load.qvs")
    assert app.loads[0].source_type.value == "SQL"


def test_simple_sql_load_field_list(parse):
    app = parse("01_simple_sql_load.qvs")
    fields = app.loads[0].fields
    assert set(fields) == {"CustomerID", "CustomerName", "Region"}, \
        f"Field list leaked junk: {fields}"


def test_field_list_does_not_leak_semicolon(parse):
    app = parse("09_comments_and_edge_cases.qvs")
    multi = next(l for l in app.loads if l.table_name == "MultiSemi")
    for f in multi.fields:
        assert ";" not in f, f"Field name contains semicolon: {f!r}"
        assert "SELECT" not in f.upper(), f"SQL keyword leaked into field name: {f!r}"


# -----------------------------------------------------------------------------
# Multiple labelled LOADs in one script
# -----------------------------------------------------------------------------

def test_multiple_loads_counted(parse):
    app = parse("02_resident_load.qvs")
    # Orders + OrdersByCustomer
    names = [l.table_name for l in app.loads]
    assert "Orders" in names
    assert "OrdersByCustomer" in names


def test_loads_do_not_bleed_into_each_other(parse):
    app = parse("09_comments_and_edge_cases.qvs")
    customer = next(l for l in app.loads if l.table_name == "Customer")
    # Should be SQL, NOT misclassified as RESIDENT from a comment further down
    assert customer.source_type.value == "SQL"


# -----------------------------------------------------------------------------
# LOAD * (star)
# -----------------------------------------------------------------------------

def test_star_load_resolves_fields_from_source_table(parse):
    app = parse("08_realistic_dashboard.qvs")
    copy = next((l for l in app.loads if l.table_name == "OrdersCopy"), None)
    assert copy is not None
    # Should inherit Orders' fields: OrderID, CustomerID, OrderDate, Amount, AmountWithTax
    assert "OrderID" in copy.fields
    assert "AmountWithTax" in copy.fields


# -----------------------------------------------------------------------------
# Preceding-LOAD chains (plan §14)
# -----------------------------------------------------------------------------

def test_preceding_load_chain_keeps_transformed_columns(parse):
    app = parse("08_realistic_dashboard.qvs")
    customer = next(l for l in app.loads if l.table_name == "Customer")
    assert "CustomerName_Upper" in customer.fields
    assert "Region" in customer.fields  # via ApplyMap


# -----------------------------------------------------------------------------
# Load order
# -----------------------------------------------------------------------------

def test_load_order_is_strictly_increasing(parse):
    app = parse("02_resident_load.qvs")
    line_numbers = [l.line_number for l in app.loads]
    assert line_numbers == sorted(line_numbers)
    assert len(set(line_numbers)) == len(line_numbers), "Load order has duplicates"
