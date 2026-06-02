"""
Shared pytest fixtures for the Tableau parser test suite.

Contract-first: if `tableau_parser` is not importable yet, all tests in this
suite skip with a single explanatory message. As the developer implements
modules from `tableau-parser-plan.md`, tests come online incrementally.
"""

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Allow the parser source to live as a sibling of this test suite (the plan's
# layout) or inside it (developer convenience).
for candidate in (PROJECT_ROOT / "tableau-parser" / "src", PROJECT_ROOT / "src"):
    if candidate.exists():
        sys.path.insert(0, str(candidate))


def pytest_configure(config):
    config.addinivalue_line("markers", "neo4j: requires a running Neo4j")
    config.addinivalue_line("markers", "slow: marks tests that take >2s")


def _parser_available() -> bool:
    try:
        importlib.import_module("tableau_parser")
        return True
    except ImportError:
        return False


PARSER_AVAILABLE = _parser_available()


def pytest_collection_modifyitems(config, items):
    """Auto-skip every test if the parser package isn't importable."""
    if PARSER_AVAILABLE:
        return
    skip = pytest.mark.skip(
        reason="tableau_parser module not importable — implement per "
               "tableau-parser-plan.md, then re-run."
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
def twb_text(fixture_path):
    """Return a callable: twb_text('01_simple_single_datasource.twb') -> str."""
    def _read(name: str) -> str:
        return fixture_path(name).read_text(encoding="utf-8")
    return _read


# --------------------------------------------------------------------------
# Neo4j gate
# --------------------------------------------------------------------------

@pytest.fixture
def neo4j_env():
    """Skip a Neo4j-marked test if env vars aren't set."""
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER")
    password = os.environ.get("NEO4J_PASSWORD")
    if not all([uri, user, password]):
        pytest.skip("NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD not set")
    return {"uri": uri, "user": user, "password": password}


@pytest.fixture
def graph_writer_mock():
    """A drop-in mock for `tableau_parser.graph.writer.GraphWriter`."""
    writer = MagicMock()
    writer.write_workbook = MagicMock(return_value={"nodes_written": 0})
    return writer


@pytest.fixture
def parse():
    """Convenience callable: `parse(path)` → `WorkbookIR`.

    Some integration tests declare `parse` as a parameter even when they import
    `parse_workbook` directly — exposing it as a fixture keeps those signatures
    happy and gives an obvious entry point for ad-hoc use.
    """
    from tableau_parser.parser.workbook import parse_workbook
    return parse_workbook
