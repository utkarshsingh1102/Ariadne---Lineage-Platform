"""
Deterministic node IDs (plan §5.4 + §15).
"""
import pytest


def test_script_id_deterministic():
    from spark_parser.utils.ids import script_id
    a = script_id("/data/inputs/orders_etl.py")
    b = script_id("/data/inputs/orders_etl.py")
    assert a == b
    assert len(a) == 16


def test_hive_table_id_matches_other_parsers():
    """Plan §5.4: lowercased FQN — must match Tableau / QlikView / TWS rules.
    A Teradata BTEQ parser writing PROD.SALES.ORDERS and a Spark script
    reading PROD.SALES.ORDERS must land on the same :Table node."""
    from spark_parser.utils.ids import table_id_hive, _canonical_table_string
    a = table_id_hive(database="PROD", schema="SALES", name="ORDERS")
    b = table_id_hive(database="prod", schema="sales", name="orders")
    assert a == b
    assert _canonical_table_string("PROD", "SALES", "Orders") == "table::prod.sales.orders"


def test_file_path_table_id_normalised():
    """Plan §5.4: trailing slash and query string must not affect the ID."""
    from spark_parser.utils.ids import table_id_path
    a = table_id_path("s3://raw/orders/")
    b = table_id_path("s3://raw/orders")
    c = table_id_path("s3://raw/orders/?v=2")
    assert a == b == c


def test_dataframe_id_uses_creation_order():
    """Plan §5.4 + §14: re-assigning `df = ...` increments creation_order
    and yields distinct IDs."""
    from spark_parser.utils.ids import dataframe_id
    a = dataframe_id(script_id="s", var_name="df", creation_order=0)
    b = dataframe_id(script_id="s", var_name="df", creation_order=1)
    assert a != b


def test_attribute_id_physical_keyed_on_table_and_column():
    from spark_parser.utils.ids import attribute_id_physical
    a = attribute_id_physical(table_fqn="PROD.SALES.ORDERS", column="amount")
    b = attribute_id_physical(table_fqn="prod.sales.orders", column="AMOUNT")
    assert a == b


def test_attribute_id_in_memory_scoped_to_dataframe():
    from spark_parser.utils.ids import attribute_id_in_memory
    a = attribute_id_in_memory(dataframe_id="df_a", column="amount")
    b = attribute_id_in_memory(dataframe_id="df_b", column="amount")
    assert a != b


def test_udf_id_scoped_to_script():
    from spark_parser.utils.ids import udf_id
    a = udf_id(script_id="s1", udf_name="tier_of")
    b = udf_id(script_id="s2", udf_name="tier_of")
    assert a != b


def test_ids_independent_of_pythonhashseed(monkeypatch):
    """Plan §15: PYTHONHASHSEED must not affect IDs."""
    from spark_parser.utils.ids import script_id
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    a = script_id("/x.py")
    monkeypatch.setenv("PYTHONHASHSEED", "42")
    b = script_id("/x.py")
    assert a == b
