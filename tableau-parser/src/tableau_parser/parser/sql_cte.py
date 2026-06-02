"""Flag-gated CTE column-lineage extraction for custom-SQL relations.

Activated by setting ``TABLEAU_RESOLVE_CTE_COLUMNS=true`` in the parser
environment. When off, custom-SQL relations still extract FROM/JOIN table
FQNs (see ``sql_parser.extract_tables``) and persist the raw SQL on the
:Table node, but per-output-column lineage is not computed.

Why it's behind a flag:

  - sqlglot's lineage resolver depends heavily on dialect quirks; without
    a fully-populated schema some columns resolve to ambiguous parents.
  - The default install ships the conservative reading (raw SQL,
    referenced tables); operators who want column-level CTE provenance
    opt in.

The function returns a list of :class:`CTEColumnIR` rows. Empty list means
"no rows we're confident about" — never raises on parse failure.
"""
from __future__ import annotations

import logging
import os

import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage as sqlglot_lineage

from tableau_parser.models.domain import CTEColumnIR

log = logging.getLogger(__name__)

_ENV_FLAG = "TABLEAU_RESOLVE_CTE_COLUMNS"


def is_enabled() -> bool:
    """Whether CTE column lineage extraction is on for this process."""
    return os.environ.get(_ENV_FLAG, "").strip().lower() in {"1", "true", "yes"}


def extract_cte_columns(
    sql: str,
    custom_sql_table_fqn: str,
    dialect: str | None = None,
) -> list[CTEColumnIR]:
    """Walk ``sql`` and emit one CTEColumnIR per resolved output column.

    Each output column of the final SELECT is traced through sqlglot's
    lineage graph back to leaves; only leaves whose source is an actual
    table column (not a literal, function, or unresolved alias) emit IR.

    The expression captured is the SQL fragment that produced the output
    column at the final-select level (e.g. ``r.gross_amount - r.discount_amount``).
    """
    if not sql or not sql.strip():
        return []
    try:
        parsed = sqlglot.parse_one(sql, read=dialect)
    except Exception as e:
        log.warning("sql_cte_parse_failed",
                    extra={"err": str(e), "sql_snippet": sql[:120]})
        return []

    # Final-select projections — the columns the relation actually exposes.
    select = parsed.find(exp.Select)
    if select is None:
        return []

    projections = select.expressions  # list[exp.Expression]
    out: list[CTEColumnIR] = []
    seen: set[tuple[str, str, str]] = set()

    for proj in projections:
        output_name = (proj.alias_or_name or "").strip()
        if not output_name:
            continue
        expression_text = proj.sql(dialect=dialect)
        try:
            node = sqlglot_lineage(output_name, sql, dialect=dialect)
        except Exception as e:
            log.warning("sql_cte_lineage_failed",
                        extra={"err": str(e), "column": output_name})
            continue

        for leaf in _walk_leaves(node):
            src_table = (leaf.get("table") or "").strip()
            src_column = (leaf.get("column") or "").strip()
            if not (src_table and src_column):
                continue
            key = (output_name, src_table, src_column)
            if key in seen:
                continue
            seen.add(key)
            out.append(CTEColumnIR(
                custom_sql_table_fqn=custom_sql_table_fqn,
                output_name=output_name,
                source_table_fqn=src_table.upper(),
                source_column=src_column,
                expression=expression_text,
            ))
    return out


def _walk_leaves(node) -> list[dict]:
    """Walk a sqlglot ``LineageNode`` tree to leaves and return their
    table/column identity. Tolerates older sqlglot APIs by reading the
    ``expression`` attribute reflectively.
    """
    if node is None:
        return []

    leaves: list[dict] = []

    def _visit(n) -> None:
        downstream = getattr(n, "downstream", None) or []
        if not downstream:
            # Leaf — extract identifier text.
            expr = getattr(n, "expression", None)
            if isinstance(expr, exp.Column):
                tbl_node = expr.table
                table_name = tbl_node if isinstance(tbl_node, str) else (
                    tbl_node.name if tbl_node else ""
                )
                col_name = expr.name or ""
                # If sqlglot resolved through a CTE, the source attribute
                # carries the originating table's full schema.
                source = getattr(n, "source", None)
                if isinstance(source, exp.Table):
                    table_name = ".".join(
                        p for p in (
                            source.args.get("catalog").name if source.args.get("catalog") else "",
                            source.args.get("db").name if source.args.get("db") else "",
                            source.name,
                        ) if p
                    )
                leaves.append({"table": table_name, "column": col_name})
            return
        for child in downstream:
            _visit(child)

    _visit(node)
    return leaves
