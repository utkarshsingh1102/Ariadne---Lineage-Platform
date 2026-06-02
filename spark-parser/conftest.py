"""
Shared pytest fixtures for the Spark parser test suite.

Contract-first: if `spark_parser` is not importable yet, every test skips with
a single explanatory message. As the developer implements modules from
`spark-parser-plan.md`, tests come online incrementally.
"""

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PYSPARK_DIR = FIXTURES_DIR / "pyspark"
SPARKSQL_DIR = FIXTURES_DIR / "sparksql"
NOTEBOOKS_DIR = FIXTURES_DIR / "notebooks"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

for candidate in (PROJECT_ROOT / "spark-parser" / "src", PROJECT_ROOT / "src"):
    if candidate.exists():
        sys.path.insert(0, str(candidate))


def pytest_configure(config):
    config.addinivalue_line("markers", "neo4j: requires a running Neo4j")
    config.addinivalue_line("markers", "slow: marks tests that take >2s")


def _parser_available() -> bool:
    try:
        importlib.import_module("spark_parser")
        return True
    except ImportError:
        return False


PARSER_AVAILABLE = _parser_available()


def pytest_collection_modifyitems(config, items):
    if PARSER_AVAILABLE:
        return
    skip = pytest.mark.skip(
        reason="spark_parser module not importable — implement per "
               "spark-parser-plan.md, then re-run."
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
def pyspark_fixture():
    def _resolve(name: str) -> Path:
        p = PYSPARK_DIR / name
        if not p.exists():
            raise FileNotFoundError(f"PySpark fixture not found: {p}")
        return p
    return _resolve


@pytest.fixture
def sparksql_fixture():
    def _resolve(name: str) -> Path:
        p = SPARKSQL_DIR / name
        if not p.exists():
            raise FileNotFoundError(f"Spark SQL fixture not found: {p}")
        return p
    return _resolve


@pytest.fixture
def notebook_fixture():
    def _resolve(name: str) -> Path:
        p = NOTEBOOKS_DIR / name
        if not p.exists():
            raise FileNotFoundError(f"Notebook fixture not found: {p}")
        return p
    return _resolve


@pytest.fixture
def read_text():
    def _read(p: Path) -> str:
        return p.read_text(encoding="utf-8")
    return _read


# --------------------------------------------------------------------------
# Neo4j gate
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def _neo4j_container():
    """Optional session-scoped Neo4j testcontainer (v0.2 §10).

    Yields a started container when ``testcontainers[neo4j]`` is installed
    AND Docker is reachable. Otherwise yields None so callers can fall back
    to env vars or skip.
    """
    if os.environ.get("NEO4J_DISABLE_TESTCONTAINERS"):
        yield None
        return
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        yield None
        return
    try:
        container = Neo4jContainer("neo4j:5.18-community")
        container.start()
    except Exception:
        yield None
        return
    try:
        yield container
    finally:
        try:
            container.stop()
        except Exception:
            pass


@pytest.fixture
def neo4j_env(_neo4j_container):
    """Connection info for the Neo4j integration tests.

    Resolution order:
      1. ``NEO4J_URI`` / ``NEO4J_USER`` / ``NEO4J_PASSWORD`` env vars.
      2. A started ``testcontainers[neo4j]`` container (v0.2 §10).
      3. Otherwise skip with a clear setup message.
    """
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USER")
    password = os.environ.get("NEO4J_PASSWORD")
    if all([uri, user, password]):
        return {"uri": uri, "user": user, "password": password}
    if _neo4j_container is not None:
        return {
            "uri": _neo4j_container.get_connection_url(),
            "user": "neo4j",
            "password": getattr(
                _neo4j_container, "password",
                os.environ.get("NEO4J_TEST_PASSWORD", "password"),
            ),
        }
    pytest.skip(
        "Neo4j not configured: set NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD, "
        "or install `testcontainers[neo4j]` and run with Docker available."
    )


@pytest.fixture
def graph_writer_mock():
    w = MagicMock()
    w.write_script = MagicMock(return_value={"nodes_written": 0})
    return w
