"""
Shared pytest fixtures for the TWS parser test suite.

Contract-first: if `tws_parser` is not importable yet, all tests skip with a
single explanatory message. As the developer implements modules from
`tws-parser-plan.md`, tests come online incrementally.
"""

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

for candidate in (PROJECT_ROOT / "tws-parser" / "src", PROJECT_ROOT / "src"):
    if candidate.exists():
        sys.path.insert(0, str(candidate))


def pytest_configure(config):
    config.addinivalue_line("markers", "neo4j: requires Neo4j (NEO4J_* env)")
    config.addinivalue_line("markers", "postgres: requires Postgres (POSTGRES_* env)")
    config.addinivalue_line("markers", "slow: marks tests that take >2s")


def _parser_available() -> bool:
    try:
        importlib.import_module("tws_parser")
        return True
    except ImportError:
        return False


PARSER_AVAILABLE = _parser_available()


def pytest_collection_modifyitems(config, items):
    if PARSER_AVAILABLE:
        return
    skip = pytest.mark.skip(
        reason="tws_parser module not importable — implement per "
               "tws-parser-plan.md, then re-run."
    )
    for item in items:
        item.add_marker(skip)


# --------------------------------------------------------------------------
# Path helpers
# --------------------------------------------------------------------------

@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def fixture_path():
    def _resolve(name: str) -> Path:
        p = FIXTURES_DIR / name
        if not p.exists():
            raise FileNotFoundError(f"Fixture not found: {p}")
        return p
    return _resolve


@pytest.fixture
def fixture_text(fixture_path):
    def _read(name: str) -> str:
        return fixture_path(name).read_text(encoding="utf-8")
    return _read


# --------------------------------------------------------------------------
# DB gates
# --------------------------------------------------------------------------

@pytest.fixture
def neo4j_env():
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER")
    password = os.environ.get("NEO4J_PASSWORD")
    if not all([uri, user, password]):
        pytest.skip("NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD not set")
    return {"uri": uri, "user": user, "password": password}


@pytest.fixture
def postgres_env():
    keys = ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
            "POSTGRES_USER", "POSTGRES_PASSWORD"]
    vals = {k: os.environ.get(k) for k in keys}
    if not all(vals.values()):
        pytest.skip("POSTGRES_* env vars not set")
    return vals


@pytest.fixture
def graph_writer_mock():
    w = MagicMock()
    w.write_schedules = MagicMock(return_value={"nodes_written": 0})
    return w


@pytest.fixture
def rdbms_writer_mock():
    w = MagicMock()
    w.write_schedules = MagicMock(return_value={"rows_written": 0})
    return w
