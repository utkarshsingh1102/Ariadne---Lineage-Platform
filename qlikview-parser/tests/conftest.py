"""
Shared pytest fixtures for the QlikView parser test suite.

Imports the parser module from the project root. Provides:
  - fixture_path(name)          : absolute path to tests/fixtures/<name>
  - parser_no_neo4j             : a QlikViewParser instance with the Neo4j driver mocked
  - parser_live_neo4j           : a real QlikViewParser (only for @pytest.mark.neo4j tests)
  - parse(name)                 : convenience to parse a fixture and return a QlikViewApp
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# --- Make the project root importable -------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def pytest_configure(config):
    """Register custom markers so pytest doesn't warn about them."""
    config.addinivalue_line("markers", "neo4j: requires a running Neo4j instance")
    config.addinivalue_line("markers", "slow: marks tests that take >2s")


# --------------------------------------------------------------------------
# Path helpers
# --------------------------------------------------------------------------

@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def fixture_path():
    """Return a callable: fixture_path('01_simple_sql_load.qvs') -> absolute Path."""
    def _resolve(name: str) -> Path:
        p = FIXTURES_DIR / name
        if not p.exists():
            raise FileNotFoundError(f"Fixture not found: {p}")
        return p
    return _resolve


# --------------------------------------------------------------------------
# Parser instances
# --------------------------------------------------------------------------

@pytest.fixture
def parser_no_neo4j():
    """
    A QlikViewParser with the Neo4j driver mocked out.
    Use this for any test that exercises parsing logic only.
    """
    from qlikview_parser import QlikViewParser

    with patch("qlikview_parser.GraphDatabase") as mock_gdb:
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = False
        mock_gdb.driver.return_value = mock_driver

        p = QlikViewParser(
            neo4j_uri="bolt://mocked:7687",
            neo4j_user="mock",
            neo4j_password="mock",
        )
        p._mock_session = mock_session  # so tests can assert on Cypher calls
        yield p


@pytest.fixture
def parser_live_neo4j():
    """
    A QlikViewParser connected to a real Neo4j (env-configured).
    Skips automatically if env vars aren't set.
    """
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER")
    password = os.environ.get("NEO4J_PASSWORD")
    if not all([uri, user, password]):
        pytest.skip("NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD not set")

    from qlikview_parser import QlikViewParser
    p = QlikViewParser(uri, user, password)
    yield p
    p.close()


# --------------------------------------------------------------------------
# Convenience: parse a fixture in one line
# --------------------------------------------------------------------------

@pytest.fixture
def parse(parser_no_neo4j, fixture_path):
    """parse('01_simple_sql_load.qvs') -> QlikViewApp"""
    def _parse(name: str):
        return parser_no_neo4j.parse_qvs_file(str(fixture_path(name)))
    return _parse
