"""parse_proxy._target_url maps source_type → URL via Settings."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from lineage_gateway import config, parse_proxy


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch):
    # Reset the cached singleton so each test gets clean Settings.
    monkeypatch.setattr(config, "_settings", None)
    yield
    monkeypatch.setattr(config, "_settings", None)


def test_target_url_for_each_parser():
    assert "tableau-parser" in parse_proxy._target_url("tableau")
    assert "tws-parser" in parse_proxy._target_url("tws")
    assert "qlikview-parser" in parse_proxy._target_url("qlikview")
    assert "spark-parser" in parse_proxy._target_url("spark")


def test_unknown_source_type_raises_400():
    with pytest.raises(HTTPException) as ei:
        parse_proxy._target_url("excel")
    assert ei.value.status_code == 400
