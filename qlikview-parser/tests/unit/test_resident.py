"""
RESIDENT load tests (plan §2.4).
A RESIDENT LOAD must emit a :QlikTable with source_type='RESIDENT' and a
DERIVES_FROM_TABLE edge to its source in-memory table.
"""
import pytest


def test_resident_source_type(parse):
    app = parse("02_resident_load.qvs")
    obc = next(l for l in app.loads if l.table_name == "OrdersByCustomer")
    assert obc.source_type.value == "RESIDENT"


def test_resident_source_table_name(parse):
    app = parse("02_resident_load.qvs")
    obc = next(l for l in app.loads if l.table_name == "OrdersByCustomer")
    assert obc.source_table == "Orders"


def test_resident_load_fields_include_aliases(parse):
    app = parse("02_resident_load.qvs")
    obc = next(l for l in app.loads if l.table_name == "OrdersByCustomer")
    # Aggregated aliases should appear as fields
    assert "TotalAmount" in obc.fields
    assert "OrderCount" in obc.fields


def test_resident_keyword_in_comment_is_ignored(parse):
    app = parse("09_comments_and_edge_cases.qvs")
    customer = next(l for l in app.loads if l.table_name == "Customer")
    assert customer.source_type.value == "SQL", \
        f"Customer mis-classified as {customer.source_type.value} due to a // Resident comment"
    assert customer.source_table != "Load"


def test_star_resident_inherits_fields(parse):
    app = parse("08_realistic_dashboard.qvs")
    copy = next((l for l in app.loads if l.table_name == "OrdersCopy"), None)
    assert copy is not None
    assert copy.source_table == "Orders"
    assert "OrderID" in copy.fields
