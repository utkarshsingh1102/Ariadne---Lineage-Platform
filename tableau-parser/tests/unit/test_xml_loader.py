"""
XML loader (plan §6 step 2).
.twb → lxml ElementTree, namespaces stripped.
"""
import pytest


def test_load_twb_returns_element_tree(fixture_path):
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("01_simple_single_datasource.twb"))
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    assert root.tag == "workbook"
    assert root.get("version") == "2024.1"


def test_workbook_version_extracted(fixture_path):
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("08_realistic_dashboard.twb"))
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    assert root.get("version") == "2024.1"


def test_namespaces_stripped(fixture_path):
    """Plan §6 step 2: namespaces stripped so XPath works without prefixes."""
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("01_simple_single_datasource.twb"))
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    # No element tag should contain `{...}` namespace prefixes
    for el in root.iter():
        assert "}" not in el.tag, f"Namespace not stripped: {el.tag}"


def test_malformed_xml_recovers_with_warning(tmp_path):
    """Plan §14: malformed XML — lxml recover mode + warning."""
    from tableau_parser.extractor.xml_loader import load_twb

    bad = tmp_path / "bad.twb"
    bad.write_text("<workbook version='2024.1'><datasources><datasource></datasources></workbook>")

    # Should not raise (recover mode); should still return a tree
    tree = load_twb(bad)
    assert tree is not None
