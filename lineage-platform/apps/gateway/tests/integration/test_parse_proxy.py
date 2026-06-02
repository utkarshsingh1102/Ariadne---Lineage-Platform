"""/parse proxies the request to the right parser via httpx."""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from lineage_gateway import neo4j_client, postgres_client


class _FakeDriver:
    def session(self, **kwargs):  # not exercised in these tests
        raise AssertionError("driver should not be touched by /parse")

    async def close(self):
        return None


@pytest.fixture
def client(monkeypatch):
    fake = _FakeDriver()

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


@respx.mock
def test_parse_dispatches_to_tableau_parser(client):
    respx.post("http://tableau-parser:8000/parse").mock(
        return_value=httpx.Response(200, json={"id": "wb-1", "stats": {"tables": 3}, "duration_ms": 12}),
    )
    r = client.post(
        "/parse",
        json={"source_type": "tableau", "file_path": "/data/inputs/foo.twbx"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "wb-1"
    assert body["source_type"] == "tableau"


@respx.mock
def test_parse_dispatches_to_spark_parser(client):
    respx.post("http://spark-parser:8000/parse").mock(
        return_value=httpx.Response(200, json={"id": "sp-1", "stats": {"dataframes": 5}, "duration_ms": 8}),
    )
    r = client.post(
        "/parse",
        json={"source_type": "spark", "file_path": "/data/inputs/etl.py"},
    )
    assert r.status_code == 200
    assert r.json()["id"] == "sp-1"


def test_unknown_source_type_returns_400(client):
    r = client.post(
        "/parse",
        json={"source_type": "excel", "file_path": "/x.xlsx"},
    )
    assert r.status_code == 400


@respx.mock
def test_upstream_failure_propagates(client):
    respx.post("http://tableau-parser:8000/parse").mock(
        return_value=httpx.Response(500, text="boom"),
    )
    r = client.post(
        "/parse",
        json={"source_type": "tableau", "file_path": "/x.twbx"},
    )
    assert r.status_code == 500


@respx.mock
def test_upstream_unreachable_yields_502(client):
    respx.post("http://qlikview-parser:8000/parse").mock(
        side_effect=httpx.ConnectError("boom"),
    )
    r = client.post(
        "/parse",
        json={"source_type": "qlikview", "file_path": "/x.qvw"},
    )
    assert r.status_code == 502
