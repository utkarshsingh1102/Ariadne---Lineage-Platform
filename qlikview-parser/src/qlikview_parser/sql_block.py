"""Stage 3 — sqlglot wrapper.

The QlikView ANTLR grammar captures ``SQL SELECT ... ;`` as a single raw
token. This module hands that text to ``sqlglot`` to lift physical table and
column references — keeping SQL dialect knowledge out of the QlikView grammar.

Phase 3 adds **column-level lineage**: for each projected output column in
the SELECT, walk its expression back to the source table-and-column it
ultimately derives from via ``sqlglot.lineage``. Aliases are unwound,
function applications become entries in the transform chain, and JOIN ON
keys are surfaced separately as FK-candidate signals feeding the v0.2
constraint engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

import sqlglot
from sqlglot import exp as sqlglot_exp
from sqlglot import lineage as sqlglot_lineage

# Tokens that sqlglot occasionally returns in Table position when it can't
# fully reason about a query — never legitimate physical tables.
_SQL_KEYWORD_BLACKLIST = {
    "SELECT", "FROM", "WHERE", "GROUP", "ORDER", "BY", "HAVING", "JOIN",
    "INNER", "LEFT", "RIGHT", "OUTER", "FULL", "ON", "AND", "OR", "NOT",
    "UNION", "ALL", "DISTINCT", "AS", "QUALIFY",
}


def _strip_sql_prefix(text: str) -> str:
    """Drop the leading ``SQL`` token so sqlglot sees a bare SELECT."""
    return re.sub(r"^\s*SQL\s+", "", text, count=1, flags=re.IGNORECASE).rstrip("; \t\r\n")


def extract_tables(sql: str, *, dialect: str | None = None) -> list[str]:
    """Return distinct table references in ``sql``.

    For each table sqlglot identifies we emit both forms — the
    fully-qualified ``catalog.db.name`` (when available) and the bare
    ``name`` — so downstream callers can pick whichever fits their
    contract. FQNs are placed first so ``result[0]`` remains the most
    specific identifier (used by the LOAD visitor to populate
    ``LoadStatement.source_table``).
    """
    if not sql or not sql.strip():
        return []
    body = _strip_sql_prefix(sql)
    try:
        trees = sqlglot.parse(body, read=dialect)
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tree in trees:
        if tree is None:
            continue
        for node in tree.find_all(sqlglot_exp.Table):
            parts = [p for p in (node.catalog, node.db, node.name) if p]
            if not parts:
                continue
            fqn = ".".join(parts)
            bare = node.name
            if bare.upper() in _SQL_KEYWORD_BLACKLIST or len(bare) < 2:
                continue
            if fqn.upper() not in seen:
                seen.add(fqn.upper())
                out.append(fqn)
            if bare.upper() not in seen:
                seen.add(bare.upper())
                out.append(bare)
    return out


def extract_columns(sql: str, *, dialect: str | None = None) -> list[str]:
    """Return distinct column references in ``sql`` (best-effort)."""
    if not sql or not sql.strip():
        return []
    body = _strip_sql_prefix(sql)
    try:
        trees = sqlglot.parse(body, read=dialect)
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tree in trees:
        if tree is None:
            continue
        for node in tree.find_all(sqlglot_exp.Column):
            name = node.name
            if not name or name == "*" or name.upper() in _SQL_KEYWORD_BLACKLIST:
                continue
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Column-level lineage (Phase 3).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnLineage:
    """A single (output_column, source_table, source_column) edge.

    ``transform_chain`` carries the function names the column passed
    through, outermost first: ``COALESCE(UPPER(name), 'X') AS clean_name``
    becomes ``('COALESCE', 'UPPER')``. ``alias`` is the projected name in
    the SELECT (which may equal ``source_column`` for a bare column).
    """
    alias: str
    source_table: str | None
    source_column: str | None
    transform_chain: tuple[str, ...] = ()
    confidence: float = 1.0


@dataclass(frozen=True)
class JoinKey:
    """A single (left_table.left_col == right_table.right_col) equi-join
    surfaced from the SQL — feeds the constraint engine's FK signal."""
    left_table: str | None
    left_column: str
    right_table: str | None
    right_column: str
    join_type: str   # INNER|LEFT|RIGHT|FULL|CROSS


def extract_column_lineage(
    sql: str, *, dialect: str | None = None,
) -> list[ColumnLineage]:
    """For each output column of the SELECT, lift its lineage back to the
    source table + column it derives from. Best-effort: returns an empty
    list when sqlglot can't fully resolve the query (CTEs without
    schema, dynamic SQL fragments, etc).

    The transform-chain captures any function applications wrapping the
    column reference, so a constraint engine downstream can decide whether
    the lineage is identity-preserving (``coalesce`` / ``cast``) or
    transformative (``hash`` / ``random``).
    """
    if not sql or not sql.strip():
        return []
    body = _strip_sql_prefix(sql)
    try:
        trees = sqlglot.parse(body, read=dialect)
    except Exception:
        return []
    out: list[ColumnLineage] = []
    for tree in trees:
        if tree is None or not isinstance(tree, sqlglot_exp.Select):
            # Walk one level for INSERT/CTE wrappers around a SELECT.
            inner = tree.find(sqlglot_exp.Select) if tree is not None else None
            if inner is None:
                continue
            select = inner
        else:
            select = tree
        from_tbls = _from_tables(select)
        default_tbl = from_tbls[0] if len(from_tbls) == 1 else None
        for projection in select.expressions:
            cl = _project_column(projection, default_table=default_tbl)
            if cl is not None:
                out.append(cl)
    return out


def _from_tables(select: sqlglot_exp.Select) -> list[str]:
    """Names of tables in the SELECT's FROM (aliases preferred over real
    names — that's what column references use)."""
    out: list[str] = []
    # sqlglot stores the FROM under "from_" (trailing underscore avoids
    # shadowing the Python keyword); fall back on the bare name for old
    # versions.
    f = select.args.get("from_") or select.args.get("from")
    if f is None:
        return out
    for tbl in f.find_all(sqlglot_exp.Table):
        label = tbl.alias_or_name
        if label and label not in out:
            out.append(label)
    return out


def _project_column(
    expr: sqlglot_exp.Expression, *, default_table: str | None = None,
) -> ColumnLineage | None:
    """Lift a single SELECT projection into a ColumnLineage record."""
    alias_name: str | None = None
    inner = expr
    if isinstance(expr, sqlglot_exp.Alias):
        alias_name = expr.alias
        inner = expr.this

    transforms: list[str] = []
    cur = inner
    # Peel function wrappers, recording each in the transform chain.
    while isinstance(cur, sqlglot_exp.Func):
        fname = cur.key.upper() if cur.key else type(cur).__name__.upper()
        transforms.append(fname)
        # Drop into the first column-shaped argument; if none, stop.
        col_child = next(
            (a for a in cur.args.values()
             if isinstance(a, sqlglot_exp.Expression) and a.find(sqlglot_exp.Column)),
            None,
        )
        if col_child is None:
            return ColumnLineage(
                alias=alias_name or _label_for(inner),
                source_table=None,
                source_column=None,
                transform_chain=tuple(transforms),
                confidence=0.5,
            )
        cur = col_child
    # ``cur`` is hopefully a Column now.
    col = cur if isinstance(cur, sqlglot_exp.Column) else cur.find(sqlglot_exp.Column)
    if col is None:
        return ColumnLineage(
            alias=alias_name or _label_for(inner),
            source_table=None,
            source_column=None,
            transform_chain=tuple(transforms),
            confidence=0.3,
        )
    src_col = col.name
    src_tbl = col.table or default_table
    return ColumnLineage(
        alias=alias_name or src_col,
        source_table=src_tbl,
        source_column=src_col,
        transform_chain=tuple(transforms),
        confidence=1.0 if not transforms else 0.8,
    )


def _label_for(node: sqlglot_exp.Expression) -> str:
    """Fallback label for an unnamed projection (``SELECT 1+1`` etc)."""
    try:
        return node.sql()[:40]
    except Exception:
        return "?"


def extract_join_keys(
    sql: str, *, dialect: str | None = None,
) -> list[JoinKey]:
    """Pull explicit ``ON left.col = right.col`` keys out of every JOIN
    in the SELECT. Each equi-join clause becomes one ``JoinKey``;
    multi-key joins yield multiple records."""
    if not sql or not sql.strip():
        return []
    body = _strip_sql_prefix(sql)
    try:
        trees = sqlglot.parse(body, read=dialect)
    except Exception:
        return []
    out: list[JoinKey] = []
    for tree in trees:
        if tree is None:
            continue
        for join in tree.find_all(sqlglot_exp.Join):
            on = join.args.get("on")
            if on is None:
                continue
            # sqlglot uses "side" for LEFT/RIGHT and "kind" for CROSS/OUTER;
            # collapse both into a single label for our IR.
            join_kind = (
                join.args.get("side") or join.args.get("kind") or "INNER"
            ).upper()
            for eq in _walk_eq_predicates(on):
                left = eq.this
                right = eq.expression
                if isinstance(left, sqlglot_exp.Column) and isinstance(right, sqlglot_exp.Column):
                    out.append(JoinKey(
                        left_table=left.table or None,
                        left_column=left.name,
                        right_table=right.table or None,
                        right_column=right.name,
                        join_type=join_kind,
                    ))
    return out


def _walk_eq_predicates(expr: sqlglot_exp.Expression) -> Iterable[sqlglot_exp.EQ]:
    """Yield every ``=`` predicate inside a JOIN's ON clause (descending
    into AND/OR conjunctions). Non-equi predicates are skipped — they
    don't fit the FK-candidate shape."""
    if isinstance(expr, sqlglot_exp.EQ):
        yield expr
        return
    for child in expr.find_all(sqlglot_exp.EQ):
        yield child
