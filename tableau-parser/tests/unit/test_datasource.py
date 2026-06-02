"""
<datasource> orchestration (plan §6 step 4).
Each <datasource> → one DatasourceIR; the parser must skip the special
'Parameters' datasource for lineage but capture it separately.
"""
import pytest


def test_single_datasource_extracted(fixture_path):
    from tableau_parser.parser.datasource import parse_datasources
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("01_simple_single_datasource.twb"))
    dses, params, _ = parse_datasources(tree)
    assert len(dses) == 1
    assert dses[0].name == "customer_ds"
    assert dses[0].caption == "Customer Data"
    assert dses[0].is_federated is False


def test_federated_flag_set(fixture_path):
    from tableau_parser.parser.datasource import parse_datasources
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("03_federated_join.twb"))
    dses, _, _ = parse_datasources(tree)
    fed = next(d for d in dses if d.name == "federated_ds")
    assert fed.is_federated is True


def test_parameters_datasource_isolated(fixture_path):
    """Plan §6 step 8: Parameters datasource → :Parameter nodes, not lineage."""
    from tableau_parser.parser.datasource import parse_datasources
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("07_parameters.twb"))
    dses, params, _ = parse_datasources(tree)

    # 'Parameters' must NOT appear among lineage datasources
    assert all(d.name != "Parameters" for d in dses)
    # …but should yield 3 :Parameter entries
    assert len(params) == 3
    names = {p.name for p in params}
    assert names == {"Param.ReportingYear", "Param.Region", "Param.IncludeTax"}


def test_realistic_dashboard_datasource_count(fixture_path):
    """4 lineage datasources + 1 Parameters block."""
    from tableau_parser.parser.datasource import parse_datasources
    from tableau_parser.extractor.xml_loader import load_twb

    tree = load_twb(fixture_path("08_realistic_dashboard.twb"))
    dses, params, _ = parse_datasources(tree)
    assert len(dses) == 4
    assert len(params) == 2


def test_has_extract_flag(fixture_path, tmp_path):
    """has_extract=True when a paired .hyper/.tde was found in the .twbx."""
    from tableau_parser.parser.datasource import parse_datasources
    from tableau_parser.extractor.xml_loader import load_twb
    from tableau_parser.extractor.archive import extract_twbx

    inner_twb = extract_twbx(fixture_path("06_packaged_workbook.twbx"), dest_dir=tmp_path)
    tree = load_twb(inner_twb)
    dses, _, _ = parse_datasources(tree, extract_dir=tmp_path)
    assert any(d.has_extract for d in dses)
