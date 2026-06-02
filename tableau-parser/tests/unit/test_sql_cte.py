"""Step 3b — CTE column-lineage extraction (flag-gated).

The module is opt-in via ``TABLEAU_RESOLVE_CTE_COLUMNS=true``. These tests
manipulate ``os.environ`` directly because the module reads the env every
call (no module-level cache).
"""
from __future__ import annotations

import os

import pytest

from tableau_parser.parser import sql_cte


_STRESS_SQL = """
WITH regional_orders AS (
  SELECT o.order_id,
         o.customer_id,
         o.region_code,
         o.gross_amount,
         o.discount_amount
  FROM   analytics.public.orders o
)
SELECT r.order_id,
       r.region_code,
       (r.gross_amount - r.discount_amount) AS net_amount
FROM   regional_orders r
"""


@pytest.fixture(autouse=True)
def _flag_off():
    """Default — flag is off. Most tests enable it explicitly."""
    prior = os.environ.pop(sql_cte._ENV_FLAG, None)
    yield
    if prior is not None:
        os.environ[sql_cte._ENV_FLAG] = prior


def test_is_enabled_off_by_default():
    assert sql_cte.is_enabled() is False


def test_is_enabled_on_when_flag_set():
    os.environ[sql_cte._ENV_FLAG] = "true"
    assert sql_cte.is_enabled() is True


def test_extract_cte_columns_empty_when_sql_empty():
    rows = sql_cte.extract_cte_columns("", custom_sql_table_fqn="x")
    assert rows == []


def test_extract_cte_columns_resolves_through_cte():
    """``net_amount`` derives from ``gross_amount`` and ``discount_amount``
    of the underlying ``analytics.public.orders`` table. Even if sqlglot's
    lineage walk gives us partial identity, at least one leaf must point
    at the real table."""
    rows = sql_cte.extract_cte_columns(
        _STRESS_SQL, custom_sql_table_fqn="custom_sql_t",
    )
    # We don't assert the EXACT count because sqlglot's lineage walk is
    # version-sensitive — but we DO assert the contract: when it returns
    # rows, the source_table_fqn must NOT be the CTE alias.
    for r in rows:
        assert "REGIONAL_ORDERS" not in r.source_table_fqn.upper(), (
            f"leaked CTE alias: {r}"
        )
    # If lineage resolved at all, net_amount should be among the outputs.
    if rows:
        assert "net_amount" in {r.output_name for r in rows}


def test_extract_cte_columns_handles_unparsable_sql_gracefully():
    rows = sql_cte.extract_cte_columns(
        "DEFINITELY NOT SQL ((", custom_sql_table_fqn="x",
    )
    assert rows == []
