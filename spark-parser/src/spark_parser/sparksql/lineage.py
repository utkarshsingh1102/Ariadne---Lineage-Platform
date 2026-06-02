"""Spark SQL lineage extraction via ``sqlglot`` (plan §6 step 8–9).

Returns ``SqlLineageIR`` — used both for ``.sql`` files and for SQL strings
embedded in ``spark.sql(...)`` calls from PySpark.

Design choices:
* ``dialect='spark'`` is the default — handles ``MERGE INTO``, ``INSERT
  OVERWRITE``, ``PARTITION``, window functions, etc.
* CTE names are extracted and *excluded* from ``source_tables``. They're
  not physical tables.
* Source-to-target column mappings are best-effort: for ``CAST(x AS y) AS
  z`` and ``CASE WHEN x ... END AS z`` we pull the referenced columns out
  of the expression subtree.
* On parse failure we return an empty IR with a warning — never raise.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp as sqlglot_exp

from ..models.domain import DerivationIR, SqlLineageIR, WarningIR


def extract_lineage(sql: str, *, dialect: str | None = "spark") -> SqlLineageIR:
    out = SqlLineageIR()
    if not sql or not sql.strip():
        return out
    try:
        trees = sqlglot.parse(sql, read=dialect)
    except Exception as e:
        out.warnings.append(WarningIR(type="sql_parse_error", detail=str(e)))
        return out

    target_seen: set[str] = set()
    source_seen: set[str] = set()
    cte_names: set[str] = set()

    for tree in trees:
        if tree is None:
            continue
        _collect_ctes(tree, cte_names)

    for tree in trees:
        if tree is None:
            continue
        # Targets — CTAS, INSERT, MERGE
        for tgt in _find_targets(tree):
            key = tgt.lower()
            if key not in target_seen:
                target_seen.add(key)
                out.target_tables.append(tgt)
        # Sources — every Table node that isn't a CTE alias or a known target
        for node in tree.find_all(sqlglot_exp.Table):
            parts = [p for p in (node.catalog, node.db, node.name) if p]
            if not parts:
                continue
            fqn = ".".join(parts)
            if node.name in cte_names and len(parts) == 1:
                continue
            key = fqn.lower()
            if key in target_seen or key in source_seen:
                continue
            source_seen.add(key)
            out.source_tables.append(fqn)

        # Derivations — projection list of the top-level SELECT
        out.derivations.extend(_extract_derivations(tree))

    return out


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

def _find_targets(tree: sqlglot_exp.Expression) -> list[str]:
    targets: list[str] = []

    # CREATE TABLE ... AS SELECT
    for create in tree.find_all(sqlglot_exp.Create):
        if create.args.get("kind", "").upper() in ("TABLE", "VIEW"):
            this = create.args.get("this")
            if isinstance(this, sqlglot_exp.Schema):
                this = this.this
            if isinstance(this, sqlglot_exp.Table):
                targets.append(_table_fqn(this))

    # INSERT INTO / INSERT OVERWRITE
    for ins in tree.find_all(sqlglot_exp.Insert):
        this = ins.args.get("this")
        if isinstance(this, sqlglot_exp.Schema):
            this = this.this
        if isinstance(this, sqlglot_exp.Table):
            targets.append(_table_fqn(this))

    # MERGE INTO
    for merge in tree.find_all(sqlglot_exp.Merge):
        this = merge.args.get("this") or merge.args.get("into")
        if isinstance(this, sqlglot_exp.Table):
            targets.append(_table_fqn(this))

    return targets


def _table_fqn(node: sqlglot_exp.Table) -> str:
    parts = [p for p in (node.catalog, node.db, node.name) if p]
    return ".".join(parts) if parts else node.name


# ---------------------------------------------------------------------------
# CTEs
# ---------------------------------------------------------------------------

def _collect_ctes(tree: sqlglot_exp.Expression, into: set[str]) -> None:
    for cte in tree.find_all(sqlglot_exp.CTE):
        alias = cte.alias
        if alias:
            into.add(alias)


# ---------------------------------------------------------------------------
# Column-level derivations
# ---------------------------------------------------------------------------

def _extract_derivations(tree: sqlglot_exp.Expression) -> list[DerivationIR]:
    derivations: list[DerivationIR] = []

    # v0.2 §4 — recursive CTE: WITH RECURSIVE cte AS (anchor UNION recursive).
    # Each column the CTE projects depends on itself via the fixpoint. Emit
    # one DerivationIR per CTE column with via="recursive_cte" so consumers
    # can see the recursion in the lineage graph.
    seen_recursive: set[str] = set()
    for with_node in tree.find_all(sqlglot_exp.With):
        if not with_node.args.get("recursive"):
            continue
        for cte in with_node.find_all(sqlglot_exp.CTE):
            alias = cte.alias
            if not alias:
                continue
            # The CTE body may be a Select OR a Union (anchor UNION recursive).
            # Grab the first SELECT inside either to enumerate projected names.
            first_select = next(iter(cte.find_all(sqlglot_exp.Select)), None)
            if first_select is None:
                continue
            for projection in first_select.expressions or []:
                name = _projection_target_name(projection)
                if not name:
                    continue
                key = f"{alias}.{name}"
                if key in seen_recursive:
                    continue
                seen_recursive.add(key)
                derivations.append(DerivationIR(
                    target_column=name,
                    source_columns=[name],
                    via="recursive_cte",
                    formula=f"{alias}.{name} (recursive)",
                ))

    # CTAS / SELECT — top-level projections
    for select in tree.find_all(sqlglot_exp.Select):
        for projection in select.expressions or []:
            d = _projection_to_derivation(projection)
            if d is not None:
                derivations.append(d)

    # v0.2 §4 — LATERAL VIEW [OUTER]. Each output column (declared in the
    # table alias) derives from every column referenced inside the explode
    # expression. ``via="lateral_view"`` (or "lateral_view_outer" for the
    # OUTER variant) so downstream consumers can distinguish.
    for lat in tree.find_all(sqlglot_exp.Lateral):
        is_outer = bool(lat.args.get("outer"))
        via = "lateral_view_outer" if is_outer else "lateral_view"
        # Source columns are whatever the explode call references.
        source_cols = _columns_in(lat.this)
        alias_node = lat.args.get("alias")
        output_cols: list[str] = []
        if alias_node is not None:
            for c in alias_node.args.get("columns") or []:
                # Identifier.name is the unquoted string for either Identifier
                # or Column wrappers sqlglot may produce.
                nm = getattr(c, "name", None)
                if nm:
                    output_cols.append(nm)
        for out_col in output_cols:
            derivations.append(DerivationIR(
                target_column=out_col,
                source_columns=source_cols,
                via=via,
                formula=lat.this.sql() if lat.this else None,
            ))

    # MERGE — `WHEN MATCHED THEN UPDATE SET col = expr` / `INSERT` clauses
    for merge in tree.find_all(sqlglot_exp.Merge):
        for when_clause in merge.args.get("whens") or merge.find_all(sqlglot_exp.When):
            then = when_clause.args.get("then") if hasattr(when_clause, "args") else None
            # UPDATE SET form
            if isinstance(then, sqlglot_exp.Update):
                for assignment in then.args.get("expressions") or []:
                    if isinstance(assignment, sqlglot_exp.EQ):
                        lhs = assignment.this
                        rhs = assignment.expression
                        if isinstance(lhs, sqlglot_exp.Column):
                            tgt = lhs.name
                            sources = _columns_in(rhs)
                            derivations.append(DerivationIR(
                                target_column=tgt,
                                source_columns=sources,
                                via="merge_update",
                            ))
            # INSERT form (column list + values list)
            if isinstance(then, sqlglot_exp.Insert):
                _maybe_extract_insert_columns(then, derivations)

    # INSERT INTO ... (col list) SELECT ...
    for ins in tree.find_all(sqlglot_exp.Insert):
        _maybe_extract_insert_columns(ins, derivations)

    return derivations


def _projection_target_name(projection: sqlglot_exp.Expression) -> str | None:
    if isinstance(projection, sqlglot_exp.Alias):
        return projection.alias
    if isinstance(projection, sqlglot_exp.Column):
        return projection.name
    return None


def _projection_to_derivation(projection: sqlglot_exp.Expression) -> DerivationIR | None:
    if isinstance(projection, sqlglot_exp.Alias):
        target = projection.alias
        expr = projection.this
        sources = _columns_in(expr)
        via = _classify_projection_via(expr)
        return DerivationIR(
            target_column=target, source_columns=sources,
            via=via, formula=expr.sql() if expr else None,
        )
    if isinstance(projection, sqlglot_exp.Column):
        return DerivationIR(
            target_column=projection.name, source_columns=[projection.name],
            via="select",
        )
    return None


def _classify_projection_via(expr: sqlglot_exp.Expression | None) -> str:
    """Pick the appropriate ``via`` for a projection (v0.2 §4).

    - "window"              : window function (``ROW_NUMBER() OVER (...)``,
                              ``RANK() OVER w``, ``... OVER (RANGE BETWEEN …)``)
    - "scalar_subquery"     : the expression *is* a subquery (e.g.,
                              ``SELECT (SELECT MAX(x) FROM t2) AS y``)
    - "correlated_subquery" : a subquery that references an outer column
    - "select"              : everything else
    """
    if expr is None:
        return "select"
    # Window functions take priority — `OVER (...)` syntax is unambiguous.
    if isinstance(expr, sqlglot_exp.Window) or expr.find(sqlglot_exp.Window):
        return "window"
    if isinstance(expr, sqlglot_exp.Subquery):
        return "correlated_subquery" if _is_correlated(expr) else "scalar_subquery"
    # Inline subquery somewhere inside an expression — still classify based on
    # the inner correlation status.
    inner = next(iter(expr.find_all(sqlglot_exp.Subquery)), None)
    if inner is not None:
        return "correlated_subquery" if _is_correlated(inner) else "scalar_subquery"
    return "select"


def _is_correlated(subquery: sqlglot_exp.Subquery) -> bool:
    """A subquery is *correlated* iff a Column inside its SELECT references a
    table alias that is not declared inside the subquery itself.

    Cheap heuristic — collects the set of FROM table names (including JOIN'd
    tables) declared inside the subquery, then any Column with a `table` not
    in that set is treated as an outer reference.
    """
    inner_tables: set[str] = set()
    for tbl in subquery.find_all(sqlglot_exp.Table):
        if tbl.alias:
            inner_tables.add(tbl.alias)
        if tbl.name:
            inner_tables.add(tbl.name)
    for col in subquery.find_all(sqlglot_exp.Column):
        ref = col.table
        if ref and ref not in inner_tables:
            return True
    return False


def _columns_in(expr: sqlglot_exp.Expression | None) -> list[str]:
    if expr is None:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for col in expr.find_all(sqlglot_exp.Column):
        if col.name and col.name not in seen:
            seen.add(col.name)
            out.append(col.name)
    return out


def _maybe_extract_insert_columns(ins, derivations: list[DerivationIR]) -> None:
    """For ``INSERT INTO t (c1, c2) SELECT s1, s2`` map sN → cN."""
    this = ins.args.get("this") if hasattr(ins, "args") else None
    if not isinstance(this, sqlglot_exp.Schema):
        return
    target_cols = [c.name for c in this.expressions or []]
    inner = ins.args.get("expression") if hasattr(ins, "args") else None
    if not isinstance(inner, sqlglot_exp.Select):
        return
    for tgt, projection in zip(target_cols, inner.expressions or []):
        sources = _columns_in(projection)
        derivations.append(DerivationIR(
            target_column=tgt, source_columns=sources,
            via="insert", formula=projection.sql() if projection else None,
        ))
