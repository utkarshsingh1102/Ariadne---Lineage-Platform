"""
<connection> block parsing (plan §2.1).
A datasource may have one <connection> or many <named-connections> (federated).
"""
import pytest


def test_single_connection_extracted(fixture_path):
    from tableau_parser.extractor.xml_loader import load_twb
    from tableau_parser.parser.connection import parse_connections

    tree = load_twb(fixture_path("01_simple_single_datasource.twb"))
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    ds = root.find(".//datasource[@name='customer_ds']")

    conns = parse_connections(ds)
    assert len(conns) == 1
    c = conns[0]
    assert c.klass == "teradata"
    assert c.server == "td-prod"
    assert c.dbname == "PROD"
    assert c.schema == "SALES"


def test_named_connections_extracted_for_federated(fixture_path):
    from tableau_parser.extractor.xml_loader import load_twb
    from tableau_parser.parser.connection import parse_connections

    tree = load_twb(fixture_path("03_federated_join.twb"))
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    ds = root.find(".//datasource[@name='federated_ds']")

    conns = parse_connections(ds)
    assert len(conns) == 2
    klasses = {c.klass for c in conns}
    assert klasses == {"teradata"}
    schemas = {c.schema for c in conns}
    assert schemas == {"SALES", "CRM"}


def test_federated_mixed_dbms(fixture_path):
    """Realistic fixture has Teradata + Oracle in one federated datasource."""
    from tableau_parser.extractor.xml_loader import load_twb
    from tableau_parser.parser.connection import parse_connections

    tree = load_twb(fixture_path("08_realistic_dashboard.twb"))
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    ds = root.find(".//datasource[@name='federated_ds']")

    conns = parse_connections(ds)
    klasses = {c.klass for c in conns}
    assert klasses == {"teradata", "oracle"}
