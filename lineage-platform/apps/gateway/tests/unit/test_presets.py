from lineage_gateway.presets import UnknownPresetError, list_presets, preset_cypher

import pytest


def test_lineage_upstream_loads():
    sql = preset_cypher("lineage-upstream")
    assert "MATCH" in sql
    assert "$node_id" in sql


def test_lineage_downstream_loads():
    sql = preset_cypher("lineage-downstream")
    assert "$node_id" in sql


def test_list_presets_returns_expected_names():
    names = list_presets()
    for required in (
        "lineage-upstream",
        "lineage-downstream",
        "tableau-physical-tables",
        "qlikview-chart-lineage",
        "spark-write-targets",
    ):
        assert required in names, f"missing preset: {required}"


def test_unknown_preset_raises():
    with pytest.raises(UnknownPresetError):
        preset_cypher("does-not-exist")


def test_directory_traversal_blocked():
    with pytest.raises(UnknownPresetError):
        preset_cypher("../../etc/passwd")
