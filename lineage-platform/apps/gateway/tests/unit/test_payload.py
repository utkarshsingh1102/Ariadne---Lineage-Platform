"""Unit tests for the Neo4j Record → GraphPayload converter.

We don't need a real driver — fake the duck-typed Node/Relationship/Path
shapes the converter walks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lineage_gateway.neo4j_client import (
    node_to_dict,
    rel_to_dict,
    records_to_graph_payload,
)


@dataclass
class FakeNode:
    labels: tuple
    props: dict
    element_id: str = "fake-node"

    def items(self):
        return self.props.items()


@dataclass
class FakeRel:
    type: str
    start_node: Any
    end_node: Any
    props: dict = field(default_factory=dict)
    element_id: str = "fake-rel"

    def items(self):
        return self.props.items()


@dataclass
class FakePath:
    nodes: list
    relationships: list


@dataclass
class FakeRecord:
    _values: list

    def values(self):
        return self._values


def test_node_to_dict_uses_id_property():
    n = FakeNode(labels=("Table",), props={"id": "abc", "name": "orders"})
    out = node_to_dict(n)
    assert out["data"]["id"] == "abc"
    assert out["data"]["label"] == "Table"
    assert out["data"]["source_system"] == "shared"


def test_source_system_inferred_from_label_prefix():
    n = FakeNode(labels=("TableauDatasource",), props={"id": "ds1"})
    out = node_to_dict(n)
    assert out["data"]["source_system"] == "tableau"

    n2 = FakeNode(labels=("QlikChart",), props={"id": "qc1"})
    assert node_to_dict(n2)["data"]["source_system"] == "qlikview"

    n3 = FakeNode(labels=("SparkScript",), props={"id": "sp1"})
    assert node_to_dict(n3)["data"]["source_system"] == "spark"

    n4 = FakeNode(labels=("TWSJob",), props={"id": "j1"})
    assert node_to_dict(n4)["data"]["source_system"] == "tws"


def test_path_walking_dedupes_nodes_and_edges():
    t = FakeNode(labels=("Table",), props={"id": "t1"})
    a = FakeNode(labels=("Attribute",), props={"id": "a1"})
    r = FakeRel(type="HAS_COLUMN", start_node=t, end_node=a)

    record1 = FakeRecord([FakePath(nodes=[t, a], relationships=[r])])
    # Same path shows up again in a second record — must not duplicate
    record2 = FakeRecord([FakePath(nodes=[t, a], relationships=[r])])

    payload = records_to_graph_payload([record1, record2])
    assert len(payload["nodes"]) == 2
    assert len(payload["edges"]) == 1
    assert payload["edges"][0]["data"]["label"] == "HAS_COLUMN"


def test_payload_handles_bare_node_value():
    n = FakeNode(labels=("Table",), props={"id": "t1"})
    payload = records_to_graph_payload([FakeRecord([n])])
    assert payload["nodes"][0]["data"]["id"] == "t1"
    assert payload["edges"] == []


def test_rel_to_dict_uses_endpoint_ids():
    t = FakeNode(labels=("Table",), props={"id": "t1"})
    a = FakeNode(labels=("Attribute",), props={"id": "a1"})
    r = FakeRel(type="HAS_COLUMN", start_node=t, end_node=a, props={"role": "dim"})
    out = rel_to_dict(r)
    assert out["data"]["source"] == "t1"
    assert out["data"]["target"] == "a1"
    assert out["data"]["properties"]["role"] == "dim"
