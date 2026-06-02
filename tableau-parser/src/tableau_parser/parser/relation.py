"""Walk a `<datasource>`'s `<relation>` tree → flat list of TableIR records.

Each TableIR carries a `relation_type` of:
  - 'table'       (direct table reference)
  - 'join'        (leaf inside a join tree)
  - 'custom_sql'  (lifted from a `type='text'` SQL body via sqlglot)
  - 'stored_proc' (lifted from a `type='stored-proc'` relation; FQN comes
                   from the inline ``<actual-name>`` child or the ``name``
                   attribute)
"""

from __future__ import annotations

from lxml import etree

from tableau_parser.models.domain import TableIR
from tableau_parser.parser import sql_parser
from tableau_parser.utils.brackets import strip_brackets
from tableau_parser.utils.ids import table_fqn, table_id
from tableau_parser.utils.lines import first_sourceline


def parse_relations(
    datasource_el: etree._Element,
    default_schema: str | None = None,
    default_database: str | None = None,
) -> list[TableIR]:
    default_schema = default_schema or ""
    default_database = default_database or ""

    out: list[TableIR] = []
    seen_ids: set[str] = set()
    # Top-level relations: direct child of <datasource> or of <connection>
    top_level: list[etree._Element] = []
    for child in datasource_el.findall("./relation"):
        top_level.append(child)
    for child in datasource_el.findall("./connection/relation"):
        top_level.append(child)

    for rel in top_level:
        _walk(rel, "top", default_database, default_schema, out, seen_ids)
    return out


def _walk(
    rel: etree._Element,
    parent_kind: str,
    default_db: str,
    default_schema: str,
    out: list[TableIR],
    seen_ids: set[str],
) -> None:
    rtype = rel.get("type", "")
    if rtype == "join":
        # Every descendant leaf is labeled relation_type='join'.
        for child in rel.findall("./relation"):
            _walk(child, "join", default_db, default_schema, out, seen_ids)
        return

    if rtype == "table":
        # parent_kind is 'join' or 'top' — that decides our relation_type label.
        label = "join" if parent_kind == "join" else "table"
        t = _build_table(rel, default_db, default_schema, label)
        if t is not None and t.id not in seen_ids:
            out.append(t)
            seen_ids.add(t.id)
        return

    if rtype == "text":
        sql = (rel.text or "").strip()
        custom_sql_line = first_sourceline(rel)
        for fqn in sql_parser.extract_tables(sql):
            db, schema, name = _split_fqn(fqn, default_db, default_schema)
            tid = table_id(db, schema, name)
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            out.append(TableIR(
                id=tid,
                name=name,
                schema=schema,
                database=db,
                fully_qualified_name=table_fqn(db, schema, name),
                relation_type="custom_sql",
                source_type="database",
                line=custom_sql_line,
                raw_sql=sql,
            ))
        return

    if rtype == "stored-proc":
        # Stored-proc identity comes from ``<actual-name>`` (the
        # ``[schema].[proc]`` or ``[db].[schema].[proc]`` form Tableau
        # serialises) or, as a fallback, the ``name`` attribute. Source
        # type is distinct from ``database`` so the writer can render the
        # proc icon and so cross-cutting tooling can treat it as a callable.
        actual = rel.findtext("./actual-name") or ""
        actual = actual.strip()
        raw = actual or rel.get("name", "")
        if raw:
            db, schema, name = _parse_table_ref(raw, default_db, default_schema)
            if name:
                tid = table_id(db, schema, name)
                if tid not in seen_ids:
                    seen_ids.add(tid)
                    out.append(TableIR(
                        id=tid,
                        name=name,
                        schema=schema,
                        database=db,
                        fully_qualified_name=table_fqn(db, schema, name),
                        relation_type="stored_proc",
                        source_type="stored_proc",
                        line=first_sourceline(rel),
                    ))
        return


def _build_table(
    rel: etree._Element, default_db: str, default_schema: str, label: str
) -> TableIR | None:
    raw = rel.get("table") or rel.get("name") or ""
    if not raw:
        return None
    db, schema, name = _parse_table_ref(raw, default_db, default_schema)
    if not name:
        return None
    return TableIR(
        id=table_id(db, schema, name),
        name=name,
        schema=schema,
        database=db,
        fully_qualified_name=table_fqn(db, schema, name),
        relation_type=label,
        source_type="database",
        line=first_sourceline(rel),
    )


def _parse_table_ref(raw: str, default_db: str, default_schema: str) -> tuple[str, str, str]:
    """`[schema].[table]` or `[db].[schema].[table]` (bracketed pieces, dot-separated)."""
    parts = [strip_brackets(p) for p in raw.split(".")]
    if len(parts) == 1:
        return default_db, default_schema, parts[0]
    if len(parts) == 2:
        return default_db, parts[0], parts[1]
    return parts[0], parts[1], parts[2]


def _split_fqn(fqn: str, default_db: str, default_schema: str) -> tuple[str, str, str]:
    parts = fqn.split(".")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return default_db, parts[0], parts[1]
    return default_db, default_schema, parts[0]
