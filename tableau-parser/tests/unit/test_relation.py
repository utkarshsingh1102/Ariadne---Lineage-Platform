"""
<relation> parsing (plan §2.1 + §6 step 4.3).
Three relation kinds:
  - type='table'  → one physical table
  - type='join'   → recursive walk over child <relation> elements
  - type='text'   → custom SQL passed to sqlglot
"""
import pytest


def test_simple_table_relation(fixture_path):
    from tableau_parser.parser.relation import parse_relations
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("01_simple_single_datasource.twb"))
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    ds = root.find(".//datasource[@name='customer_ds']")

    tables = parse_relations(ds, default_schema="SALES", default_database="PROD")
    assert len(tables) == 1
    t = tables[0]
    assert t.name == "Customer"
    assert t.schema == "SALES"
    assert t.database == "PROD"
    assert t.fully_qualified_name.upper() == "PROD.SALES.CUSTOMER"


def test_join_relation_yields_each_leaf(fixture_path):
    from tableau_parser.parser.relation import parse_relations
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("03_federated_join.twb"))
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    ds = root.find(".//datasource[@name='federated_ds']")

    tables = parse_relations(ds, default_schema=None, default_database="PROD")
    names = {t.name for t in tables}
    assert names == {"Orders", "Customer"}


def test_join_relation_records_relation_type(fixture_path):
    """The READS_TABLE edge needs `relation_type='join'`."""
    from tableau_parser.parser.relation import parse_relations
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("03_federated_join.twb"))
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    ds = root.find(".//datasource[@name='federated_ds']")
    tables = parse_relations(ds, default_schema=None, default_database="PROD")
    for t in tables:
        assert t.relation_type == "join"


def test_custom_sql_relation_lifts_physical_tables(fixture_path):
    """Plan §6 step 4.3: type='text' passes the SQL through sqlglot."""
    from tableau_parser.parser.relation import parse_relations
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("04_custom_sql.twb"))
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    ds = root.find(".//datasource[@name='custom_sql_ds']")

    tables = parse_relations(ds, default_schema="SALES", default_database="PROD")
    fqns = {t.fully_qualified_name.upper() for t in tables}
    assert "PROD.SALES.ORDERS" in fqns
    assert "PROD.CRM.CUSTOMER" in fqns
    assert "PROD.INVENTORY.PRODUCT" in fqns
    for t in tables:
        assert t.relation_type == "custom_sql"


def test_nested_join_recurses(tmp_path):
    """Plan §9.1: 3-table nested join."""
    from tableau_parser.parser.relation import parse_relations
    from tableau_parser.extractor.xml_loader import load_twb

    snippet = tmp_path / "nested.twb"
    snippet.write_text("""<?xml version='1.0'?>
<workbook version='2024.1'><datasources><datasource name='ds' inline='true'>
  <connection class='teradata' dbname='PROD' schema='SALES'/>
  <relation join='inner' type='join'>
    <relation join='left' type='join'>
      <relation name='A' table='[S].[A]' type='table'/>
      <relation name='B' table='[S].[B]' type='table'/>
    </relation>
    <relation name='C' table='[S].[C]' type='table'/>
  </relation>
</datasource></datasources></workbook>""")

    tree = load_twb(snippet)
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    ds = root.find(".//datasource[@name='ds']")
    tables = parse_relations(ds, default_schema="S", default_database="PROD")
    names = {t.name for t in tables}
    assert names == {"A", "B", "C"}
