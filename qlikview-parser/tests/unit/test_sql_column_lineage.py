"""Phase 3 — column-level SQL lineage extracted via sqlglot.

Verifies that:
1. A simple ``SELECT id, name FROM t`` produces one ColumnLineage per
   output column with source_table='t', source_column={'id','name'}.
2. Aliased projections preserve the alias as the output name.
3. Function wrappers populate ``transform_chain`` outermost-first.
4. JOIN ON keys are surfaced via ``extract_join_keys`` as separate
   FK-candidate signals.
5. Multi-key joins yield multiple JoinKey records.
6. Unparseable SQL degrades to an empty list (no exceptions).
"""
from __future__ import annotations

from qlikview_parser.sql_block import (
    ColumnLineage,
    JoinKey,
    extract_column_lineage,
    extract_join_keys,
)


# ---- ColumnLineage --------------------------------------------------------

def test_bare_select_extracts_one_lineage_per_column():
    cls = extract_column_lineage("SELECT id, name FROM customers;")
    assert len(cls) == 2
    by_alias = {c.alias: c for c in cls}
    assert by_alias["id"].source_table == "customers"
    assert by_alias["id"].source_column == "id"
    assert by_alias["id"].transform_chain == ()
    assert by_alias["name"].source_column == "name"


def test_aliased_column_uses_alias_as_output_name():
    cls = extract_column_lineage(
        "SELECT id AS customer_id, name AS customer_name FROM customers;"
    )
    aliases = {c.alias for c in cls}
    assert aliases == {"customer_id", "customer_name"}
    assert next(c for c in cls if c.alias == "customer_id").source_column == "id"


def test_function_wrappers_populate_transform_chain():
    cls = extract_column_lineage(
        "SELECT UPPER(name) AS clean_name FROM customers;"
    )
    assert len(cls) == 1
    assert cls[0].alias == "clean_name"
    assert cls[0].source_column == "name"
    assert "UPPER" in cls[0].transform_chain


def test_nested_functions_chain_outermost_first():
    cls = extract_column_lineage(
        "SELECT COALESCE(UPPER(name), 'X') AS norm FROM customers;"
    )
    assert cls[0].alias == "norm"
    # COALESCE wraps UPPER; outermost first.
    assert cls[0].transform_chain[0] == "COALESCE"
    assert "UPPER" in cls[0].transform_chain
    # Source still resolves to the underlying column.
    assert cls[0].source_column == "name"


def test_qualified_table_reference_preserved():
    cls = extract_column_lineage(
        "SELECT t.id FROM core.customers AS t;"
    )
    assert cls[0].source_column == "id"
    assert cls[0].source_table == "t"


def test_unparseable_sql_returns_empty_list():
    assert extract_column_lineage("not a sql statement at all") == []
    assert extract_column_lineage("") == []


def test_sql_prefix_is_stripped():
    """QlikView's ``SQL SELECT…`` prefix must not break extraction."""
    cls = extract_column_lineage("SQL SELECT id FROM customers;")
    assert len(cls) == 1
    assert cls[0].source_column == "id"


# ---- JoinKey --------------------------------------------------------------

def test_simple_join_extracts_one_key():
    keys = extract_join_keys(
        "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id;"
    )
    assert len(keys) == 1
    k = keys[0]
    assert k.left_table == "o" and k.left_column == "customer_id"
    assert k.right_table == "c" and k.right_column == "id"
    assert k.join_type == "INNER"


def test_left_join_records_join_kind():
    keys = extract_join_keys(
        "SELECT * FROM orders o LEFT JOIN customers c ON o.cid = c.id;"
    )
    assert keys[0].join_type == "LEFT"


def test_multi_key_join_yields_multiple_records():
    keys = extract_join_keys(
        "SELECT * FROM a JOIN b ON a.x = b.x AND a.y = b.y;"
    )
    cols = {(k.left_column, k.right_column) for k in keys}
    assert ("x", "x") in cols
    assert ("y", "y") in cols


def test_non_equi_predicate_is_skipped():
    """``ON a.x > b.x`` isn't FK-shaped — must not appear in join keys."""
    keys = extract_join_keys(
        "SELECT * FROM a JOIN b ON a.x > b.x;"
    )
    assert keys == []


def test_unparseable_sql_returns_empty_join_keys():
    assert extract_join_keys("not sql") == []
