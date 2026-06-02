"""
Deterministic node-ID generation (plan §5.4).
Every node label has a canonical string that hashes to a stable 16-char SHA-256
prefix. These IDs are the cross-parser merge key.
"""
import pytest


def test_workbook_id_deterministic():
    from tableau_parser.utils.ids import workbook_id
    a = workbook_id("/data/inputs/sales.twbx")
    b = workbook_id("/data/inputs/sales.twbx")
    assert a == b
    assert len(a) == 16


def test_table_id_uses_fqn():
    """Plan §5.4: Table IDs must come from `db.schema.name` lowercased."""
    from tableau_parser.utils.ids import table_id
    # Mixed case input → consistent output
    a = table_id(database="PROD", schema="SALES", name="ORDERS")
    b = table_id(database="prod", schema="sales", name="orders")
    assert a == b


def test_table_id_matches_other_parsers_shape():
    """Cross-parser contract: Tableau and Ab Initio / Teradata parsers must
    derive the *same* ID for the same physical table."""
    from tableau_parser.utils.ids import table_id, _canonical_table_string
    # Canonical string must be 'table::prod.sales.orders'
    assert _canonical_table_string("PROD", "SALES", "Orders") == "table::prod.sales.orders"


def test_attribute_id_physical_keyed_on_table_and_column():
    from tableau_parser.utils.ids import attribute_id_physical
    a = attribute_id_physical(table_fqn="PROD.SALES.ORDERS", column="OrderID")
    b = attribute_id_physical(table_fqn="prod.sales.orders", column="orderid")
    assert a == b


def test_attribute_id_calculated_scoped_to_datasource():
    """Plan §5.4: calculated-field IDs are scoped to the datasource, not the
    workbook, so the same field name in two datasources gets two IDs."""
    from tableau_parser.utils.ids import attribute_id_calculated
    a = attribute_id_calculated(datasource_id="ds_one", field_name="Profit")
    b = attribute_id_calculated(datasource_id="ds_two", field_name="Profit")
    assert a != b


def test_connection_id_keyed_on_class_server_db():
    from tableau_parser.utils.ids import connection_id
    a = connection_id(klass="teradata", server="td-prod", dbname="PROD")
    b = connection_id(klass="teradata", server="td-prod", dbname="PROD")
    assert a == b
    c = connection_id(klass="oracle", server="td-prod", dbname="PROD")
    assert a != c
