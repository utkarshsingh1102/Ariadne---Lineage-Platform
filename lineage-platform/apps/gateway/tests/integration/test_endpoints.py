"""End-to-end-ish tests against the FastAPI app with stubbed backends.

The Neo4j driver is replaced with an async mock that yields known
records; httpx is patched via respx for /parse proxy tests.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
from fastapi.testclient import TestClient

from lineage_gateway import neo4j_client, postgres_client


# ---------------------------------------------------------------------------
# Driver / pool stubs
# ---------------------------------------------------------------------------


class _FakeNode:
    def __init__(self, labels, props, element_id="x"):
        self.labels = labels
        self._props = props
        self.element_id = element_id

    def items(self):
        return self._props.items()


class _FakeResult:
    def __init__(self, records):
        self._records = records

    def __aiter__(self):
        async def gen():
            for r in self._records:
                yield r
        return gen()

    async def consume(self):
        return None


class _FakeRecord:
    def __init__(self, values_dict):
        self._d = values_dict

    def values(self):
        return list(self._d.values())

    def value(self):
        return next(iter(self._d.values()))

    def __iter__(self):
        return iter(self._d.items())

    def keys(self):
        return self._d.keys()


class _FakeSession:
    def __init__(self, records_by_query=None):
        self.records_by_query = records_by_query or {}

    async def run(self, cypher, params=None, **kwargs):
        # Match by substring so test setup stays terse.
        for key, recs in self.records_by_query.items():
            if key in cypher:
                return _FakeResult(recs)
        return _FakeResult([])

    async def close(self):
        return None


class _FakeDriver:
    def __init__(self, records_by_query=None):
        self._recs = records_by_query

    def session(self, **kwargs):
        return _FakeSession(self._recs)

    async def close(self):
        return None


@pytest.fixture
def client(monkeypatch):
    # Install a fake driver that returns canned records keyed by Cypher substring.
    records = {
        "db.labels()": [_FakeRecord({"label": "Table"}), _FakeRecord({"label": "TableauDatasource"})],
        "db.relationshipTypes()": [_FakeRecord({"t": "READS_TABLE"})],
        "db.propertyKeys()": [_FakeRecord({"k": "name"})],
        "MATCH (n:`Table`)": [_FakeRecord({"n": _FakeNode(("Table",), {"id": "t1", "name": "orders"})})],
        "MATCH (n) ": [_FakeRecord({"n": _FakeNode(("Table",), {"id": "t1", "name": "orders"})})],
        "RETURN 1": [_FakeRecord({"v": 1})],
    }
    fake = _FakeDriver(records)

    # Neutralise lifespan so it doesn't clobber our fake driver / open real connections.
    async def _noop_init_driver(_settings):
        neo4j_client._driver = fake

    async def _noop_init_pool(_settings):
        postgres_client._pool = None

    async def _noop_close():
        return None

    monkeypatch.setattr(neo4j_client, "init_driver", _noop_init_driver)
    monkeypatch.setattr(neo4j_client, "close_driver", _noop_close)
    monkeypatch.setattr(postgres_client, "init_pool", _noop_init_pool)
    monkeypatch.setattr(postgres_client, "close_pool", _noop_close)
    monkeypatch.setattr(neo4j_client, "_driver", fake)
    monkeypatch.setattr(postgres_client, "_pool", None)

    from lineage_gateway.main import app

    with TestClient(app) as c:
        yield c


def test_health_reports_degraded_when_postgres_unreachable(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["neo4j"] == "connected"
    assert body["postgres"] == "unreachable"
    assert body["status"] == "degraded"


def test_version(client):
    r = client.get("/version")
    assert r.status_code == 200
    assert r.json()["gateway"] == "lineage-gateway"


def test_graph_schema(client):
    r = client.get("/graph/schema")
    assert r.status_code == 200
    body = r.json()
    assert "Table" in body["labels"]
    assert "READS_TABLE" in body["relationship_types"]


def test_graph_nodes_with_label(client):
    r = client.get("/graph/nodes?label=Table")
    assert r.status_code == 200
    body = r.json()
    assert body["nodes"]
    assert body["nodes"][0]["data"]["label"] == "Table"


def test_graph_nodes_rejects_bad_label(client):
    r = client.get("/graph/nodes?label=Table;DROP")
    assert r.status_code == 400


def test_cypher_endpoint_blocks_writes(client):
    r = client.post(
        "/graph/query/cypher",
        json={"cypher": "MATCH (n) DETACH DELETE n"},
    )
    assert r.status_code == 400
    assert "DELETE" in r.json()["detail"] or "DETACH" in r.json()["detail"]


def test_cypher_endpoint_allows_reads(client):
    r = client.post(
        "/graph/query/cypher",
        json={"cypher": "MATCH (n) RETURN n LIMIT 1"},
    )
    assert r.status_code == 200


def test_preset_listing(client):
    r = client.get("/graph/query/presets")
    assert r.status_code == 200
    assert "lineage-upstream" in r.json()["presets"]


def test_unknown_preset_returns_404(client):
    r = client.post("/graph/query/preset/bogus?node_id=t1")
    assert r.status_code == 404


def test_tws_jobs_returns_503_when_pg_down(client):
    r = client.get("/tws/jobs")
    assert r.status_code == 503
