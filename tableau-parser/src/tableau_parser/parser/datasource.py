"""Top-level datasource walker.

Returns `(datasources, parameters)`. The special `Parameters` datasource is
isolated and surfaced separately as `:Parameter` IRs.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from tableau_parser.extractor.archive import detect_extracts
from tableau_parser.models.domain import (
    DatasourceIR,
    HasColumnIR,
    ParameterIR,
    ParameterScopeIR,
    ReadsTableIR,
)
from tableau_parser.parser import (
    calculation,
    column,
    connection,
    derived,
    relation,
    sql_cte,
    worksheet,
)
from tableau_parser.utils.brackets import strip_brackets
from tableau_parser.utils.ids import datasource_id, parameter_id, parameter_scope_id
from tableau_parser.utils.lines import first_sourceline


def parse_datasources(
    tree,
    *,
    workbook_id_str: str = "",
    extract_dir: str | Path | None = None,
) -> tuple[list[DatasourceIR], list[ParameterIR], list[ParameterScopeIR]]:
    """Resolution-plan §2.1: anchor datasource *definitions* to a direct
    child of root. The previous ``.//datasources/datasource`` form matched
    nested ``<view><datasources><datasource>`` *reference* nodes inside
    worksheets, inflating the datasource count. A node's definition is
    always found by an anchored path; ``.//`` is reserved for collecting
    references.

    Improvement-v2 §4 also returns a list of ``ParameterScopeIR`` — one
    synthetic node per ``<datasource name='Parameters'>`` block. Real
    user datasources land in the first list as before.
    """
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    extracts_present = bool(detect_extracts(extract_dir)) if extract_dir else False

    datasources: list[DatasourceIR] = []
    parameters: list[ParameterIR] = []
    parameter_scopes: list[ParameterScopeIR] = []

    # Anchored: only direct ``<workbook>/<datasources>/<datasource>`` children.
    ds_elements = root.findall("./datasources/datasource")

    for ds_el in ds_elements:
        name = ds_el.get("name", "")
        if not name:
            continue
        # Parameters are typically isolated into the special ``Parameters``
        # datasource, but real workbooks also embed param columns (with the
        # ``param-domain-type`` attribute) inside user datasources. Harvest
        # both shapes — see §2.2.
        if name == "Parameters":
            scope = ParameterScopeIR(
                id=parameter_scope_id(workbook_id_str, name),
                name=name,
                workbook_id=workbook_id_str,
                line=first_sourceline(ds_el),
            )
            parameter_scopes.append(scope)
            scoped = _parse_parameters(ds_el, workbook_id_str, scope_id=scope.id)
            parameters.extend(scoped)
            continue
        # User datasource: extract its param-columns first, then build the
        # regular DatasourceIR. ``_parse_one`` skips param columns via
        # column.parse_columns' filter so the same column never lands in both
        # the parameter list and the field list. These parameters live
        # OUTSIDE the synthetic ParameterScope (they're embedded in a
        # user datasource), so scope_id stays empty.
        parameters.extend(_parse_parameters(ds_el, workbook_id_str))
        datasources.append(_parse_one(ds_el, workbook_id_str, extracts_present))

    return datasources, parameters, parameter_scopes


def _is_parameter_column(col: etree._Element, *, in_parameters_ds: bool) -> bool:
    """Resolution-plan §2.2: a column is a parameter iff it carries the
    ``param-domain-type`` attribute. Columns inside the special
    ``Parameters`` datasource are *also* treated as parameters because
    Tableau sometimes omits the attribute there.
    """
    if col.get("param-domain-type"):
        return True
    return in_parameters_ds


def _parse_parameters(
    ds_el: etree._Element,
    workbook_id_str: str,
    *,
    scope_id: str = "",
) -> list[ParameterIR]:
    """Emit a ParameterIR for every ``<column param-domain-type=...>`` in
    the given datasource element. Also accepts every column when ``ds_el``
    is the special ``Parameters`` datasource — see ``_is_parameter_column``.

    ``scope_id`` is set when the parameters live under the synthetic
    :TableauParameterScope node (the ``Parameters`` block). Parameters
    that come from inside user datasources leave it empty.
    """
    in_parameters_ds = ds_el.get("name", "") == "Parameters"
    out: list[ParameterIR] = []
    for col in ds_el.findall("./column"):
        if not _is_parameter_column(col, in_parameters_ds=in_parameters_ds):
            continue
        n = strip_brackets(col.get("name", ""))
        if not n:
            continue
        out.append(ParameterIR(
            id=parameter_id(workbook_id_str, n),
            name=n,
            workbook_id=workbook_id_str,
            datatype=col.get("datatype", ""),
            current_value=col.get("value", ""),
            line=first_sourceline(col),
            scope_id=scope_id,
        ))
    return out


def _parse_one(
    ds_el: etree._Element, workbook_id_str: str, extracts_present: bool
) -> DatasourceIR:
    name = ds_el.get("name", "")
    caption = ds_el.get("caption", "")
    is_federated = (ds_el.find("./connection[@class='federated']") is not None) \
                   or (ds_el.find("./named-connections") is not None)

    ds_id = datasource_id(workbook_id_str, name)

    conns = connection.parse_connections(ds_el)
    default_db = conns[0].dbname if conns else ""
    default_schema = conns[0].schema if conns else ""

    tables = relation.parse_relations(
        ds_el, default_schema=default_schema, default_database=default_db
    )

    # Physical fields can only be confidently attributed to *one* table.
    primary_table_fqn = tables[0].fully_qualified_name if len(tables) == 1 else None
    fields = column.parse_columns(ds_el, ds_id, primary_table_fqn=primary_table_fqn)

    # Improvement-v2 §2 — stored-proc relations carry their physical
    # columns inside ``<relation><columns><column>`` rather than at the
    # datasource level. Lift them here so they reach the symbol table
    # used by formula-reference resolution.
    proc_fields = column.parse_proc_columns(ds_el, ds_id, tables)
    fields.extend(proc_fields)

    has_cols: list[HasColumnIR] = []
    if primary_table_fqn:
        tid = tables[0].id
        for f in fields:
            if not f.is_calculated and f.table_fqn == primary_table_fqn:
                has_cols.append(HasColumnIR(table_id=tid, field_id=f.id))
    # Proc-declared columns attach to their owning proc table even when
    # the datasource has multiple tables (so the per-table HasColumnIR
    # still lands).
    proc_table_by_fqn = {
        t.fully_qualified_name: t for t in tables if t.relation_type == "stored_proc"
    }
    for f in proc_fields:
        proc_t = proc_table_by_fqn.get(f.table_fqn)
        if proc_t is not None and not any(
            hc.table_id == proc_t.id and hc.field_id == f.id for hc in has_cols
        ):
            has_cols.append(HasColumnIR(table_id=proc_t.id, field_id=f.id))

    # Per-reference read line — comes from the same XML node the table came
    # from, so the source-code panel can scroll to the relation element.
    table_lines: dict[str, int | None] = {t.id: t.line for t in tables}
    reads = [
        ReadsTableIR(
            datasource_id=ds_id, table_id=t.id, relation_type=t.relation_type,
            line=table_lines.get(t.id),
        )
        for t in tables
    ]

    has_extract = extracts_present or (ds_el.get("hasextract", "").lower() == "true")

    # Improvement-v2 §3b — opt-in CTE column lineage. Only runs against
    # ``relation_type='custom_sql'`` rows that carry a non-empty raw_sql
    # (i.e. the ones lifted from <relation type='text'>).
    cte_cols: list = []
    if sql_cte.is_enabled():
        for t in tables:
            if t.relation_type == "custom_sql" and t.raw_sql:
                cte_cols.extend(sql_cte.extract_cte_columns(
                    t.raw_sql, t.fully_qualified_name,
                ))

    ds = DatasourceIR(
        id=ds_id,
        name=name,
        workbook_id=workbook_id_str,
        caption=caption,
        is_federated=is_federated,
        has_extract=has_extract,
        connections=conns,
        tables=tables,
        fields=fields,
        has_columns=has_cols,
        reads_tables=reads,
        filters=worksheet.parse_datasource_filters(ds_el, name),
        groups=derived.parse_groups(ds_el, ds_id),
        sets=derived.parse_sets(ds_el, ds_id),
        bins=derived.parse_bins(ds_el, ds_id),
        hierarchies=derived.parse_hierarchies(ds_el, ds_id),
        cte_columns=cte_cols,
        line=first_sourceline(ds_el),
    )
    ds.derives_from = calculation.resolve_dependencies(ds)
    return ds
