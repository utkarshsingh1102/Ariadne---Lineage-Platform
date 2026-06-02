"""sqlglot wrapper for the bodies of `<relation type="text">` custom-SQL blocks."""

from __future__ import annotations

import logging

import sqlglot
from sqlglot import exp

log = logging.getLogger(__name__)


def extract_tables(sql: str, dialect: str | None = None) -> list[str]:
    """Parse `sql` and return distinct physical-table FQNs (uppercased).

    - CTE aliases are excluded — only base tables that resolve to physical names
      come back. The implementation relies on sqlglot's expansion of `with`
      definitions when computing scopes.
    - Returns `[]` on parse failure (logged, not raised).
    """
    if not sql or not sql.strip():
        return []
    try:
        parsed = sqlglot.parse_one(sql, read=dialect)
    except Exception as e:
        log.warning("sql_parse_failed", extra={"err": str(e), "sql_snippet": sql[:120]})
        return []

    # Collect CTE/derived-subquery aliases so we can filter them out.
    cte_aliases: set[str] = set()
    for cte in parsed.find_all(exp.CTE):
        alias = cte.alias_or_name
        if alias:
            cte_aliases.add(alias.upper())

    out: list[str] = []
    seen: set[str] = set()
    for table in parsed.find_all(exp.Table):
        name = (table.name or "").strip()
        if not name:
            continue
        if name.upper() in cte_aliases:
            continue
        schema = (table.args.get("db").name if table.args.get("db") else "") or ""
        db = (table.args.get("catalog").name if table.args.get("catalog") else "") or ""
        parts = [p for p in (db, schema, name) if p]
        fqn = ".".join(parts).upper()
        if fqn not in seen:
            seen.add(fqn)
            out.append(fqn)
    return out
