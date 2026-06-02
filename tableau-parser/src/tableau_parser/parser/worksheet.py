"""Top-level worksheet walker.

Builds a WorksheetIR for every `<worksheet>` in the tree:
- per-(field, shelf) usage rows (multi-shelf no longer lossy)
- aggregation inferred from inline ``SUM([X])`` patterns
- `<filter>` and `<sort>` children captured as their own IR rows
"""

from __future__ import annotations

import re

from lxml import etree

from tableau_parser.models.domain import (
    FieldUsageIR,
    WorksheetBlendIR,
    WorksheetFilterIR,
    WorksheetIR,
    WorksheetSortIR,
)
from tableau_parser.utils.brackets import find_refs, strip_brackets
from tableau_parser.utils.ids import worksheet_blend_id, worksheet_id
from tableau_parser.utils.lines import first_sourceline


# Shelves are nominal tags inside <worksheet>/<table>/<view>. Each one we
# walk produces a (field, shelf) usage row.
_SHELF_TAGS = ("rows", "cols", "filter", "color", "size", "label", "detail", "tooltip", "shape")

# Match a Tableau aggregation wrapper. Handles both single-bracket
# arguments (``SUM([Sales])``) and two-part dotted arguments
# (``SUM([sales_ds].[order_amount])``). For the dotted form the regex
# captures the LAST bracket pair so the aggregation lands on the actual
# field rather than the datasource name.
_AGG_WRAPPER_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]+)\s*\(\s*"
    r"(?:\[[^\[\]]+\]\.)?"      # optional ``[ds].`` prefix, not captured
    r"\[([^\[\]]+)\]"           # actual field name
    r"\s*\)",
    re.IGNORECASE,
)


def parse_worksheets(tree, workbook_id_str: str = "") -> list[WorksheetIR]:
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    out: list[WorksheetIR] = []
    for ws_el in root.findall(".//worksheets/worksheet"):
        ir = _build(ws_el, workbook_id_str)
        if ir is not None:
            out.append(ir)
    return out


def _build(ws_el: etree._Element, workbook_id_str: str) -> WorksheetIR | None:
    name = ws_el.get("name", "")
    if not name:
        return None
    wsid = worksheet_id(workbook_id_str, name)

    # Walk shelves first so we know what the per-shelf field+aggregation map
    # looks like before we build the canonical FieldUsageIR list below.
    shelf_rows: list[tuple[str, str, str, int | None]] = []  # (field, shelf, agg, line)
    for shelf_tag in _SHELF_TAGS:
        for shelf_el in ws_el.iter(shelf_tag):
            text = (shelf_el.text or "").strip()
            if not text:
                continue
            sl = first_sourceline(shelf_el)
            # Pair every `[field]` with whatever aggregation wraps it on
            # this shelf. ``[a].[b]`` two-part refs collapse to the last
            # bracketed name — same convention as before.
            agg_by_field: dict[str, str] = {}
            for m in _AGG_WRAPPER_RE.finditer(text):
                agg_by_field[m.group(2)] = m.group(1).upper()
            for ref in find_refs(text):
                shelf_rows.append((ref, shelf_tag, agg_by_field.get(ref, ""), sl))

    # Build the canonical USES_FIELD rows from `<datasource-dependencies>`
    # — these are the authoritative field bindings, including the
    # datasource scope. Pair every (ds, field) with EVERY shelf it lives on
    # (no longer dedup'd to one shelf per field — see plan §4).
    usages: list[FieldUsageIR] = []
    for dep in ws_el.findall(".//datasource-dependencies"):
        ds_name = dep.get("datasource", "")
        for col in dep.findall("./column"):
            field_name = strip_brackets(col.get("name", ""))
            if not field_name:
                continue
            matching = [r for r in shelf_rows if r[0] == field_name]
            if matching:
                for _, shelf, agg, sl in matching:
                    usages.append(FieldUsageIR(
                        field_name=field_name,
                        shelf=shelf,
                        datasource_name=ds_name,
                        line=sl or first_sourceline(col),
                        aggregation=agg,
                    ))
            else:
                # Field is declared as a dependency but doesn't actually
                # appear on a shelf (it might be referenced via a calc
                # field on the canvas). Emit a single "unknown" usage so
                # the existing tests (which expect one entry per declared
                # field) still pass.
                usages.append(FieldUsageIR(
                    field_name=field_name,
                    shelf="unknown",
                    datasource_name=ds_name,
                    line=first_sourceline(col),
                ))

    filters = _parse_filters(ws_el, worksheet_id_str=wsid)
    sorts = _parse_sorts(ws_el, worksheet_id_str=wsid)
    blends = _parse_blends(ws_el, worksheet_id_str=wsid)

    return WorksheetIR(
        id=wsid, name=name, workbook_id=workbook_id_str,
        field_usages=usages, filters=filters, sorts=sorts,
        blends=blends,
        line=first_sourceline(ws_el),
    )


def _parse_blends(
    ws_el: etree._Element, *, worksheet_id_str: str,
) -> list[WorksheetBlendIR]:
    """Improvement-v2 §9 — ``<datasource-relationship>`` blends.

    Tableau wraps each blend in a ``<relation primary='...' secondary='...'>``
    inside ``<datasource-relationship>``. Linked field names live inside
    ``<clause>/<expression>`` two-part refs like ``[ds1].[fieldA] = [ds2].[fieldA]``.
    """
    out: list[WorksheetBlendIR] = []
    for rel in ws_el.iter("relation"):
        # Only datasource-blend relations have these attrs — physical
        # joins use ``type='join'`` instead.
        primary = rel.get("primary", "")
        secondary = rel.get("secondary", "")
        if not (primary and secondary):
            continue
        # The linked field names come from the inner two-part expression
        # operands. Tableau emits ``[ds].[field]`` in the ``op`` attr.
        on_fields: list[str] = []
        seen: set[str] = set()
        for expr in rel.iter("expression"):
            op = expr.get("op", "")
            if "." not in op or "[" not in op:
                continue
            refs = find_refs(op)
            if len(refs) >= 2:
                # Two-part ``[ds].[field]`` → take the field (last part).
                fname = refs[-1]
                if fname and fname not in seen:
                    seen.add(fname)
                    on_fields.append(fname)
        out.append(WorksheetBlendIR(
            id=worksheet_blend_id(worksheet_id_str, primary, secondary),
            worksheet_id=worksheet_id_str,
            primary_datasource_name=primary,
            secondary_datasource_name=secondary,
            on_field_names=on_fields,
            line=first_sourceline(rel),
        ))
    return out


def _parse_filters(
    ws_el: etree._Element, *, worksheet_id_str: str = "", datasource_id_str: str = "",
) -> list[WorksheetFilterIR]:
    """Walk every `<filter>` element under ``ws_el`` and emit one row per
    bracketed field reference its `column` attribute points at."""
    out: list[WorksheetFilterIR] = []
    for fil in ws_el.iter("filter"):
        # A filter's ``column`` attr looks like ``[ds].[field]``.
        col = fil.get("column", "")
        refs = find_refs(col)
        if not refs:
            continue
        field_name = refs[-1]
        ds_name = refs[0] if len(refs) >= 2 else ""
        # Inline expression body — grab the raw text of the filter element
        # so the writer can persist it for human review.
        expr = etree.tostring(fil, encoding="unicode")[:512]
        out.append(WorksheetFilterIR(
            field_name=field_name,
            datasource_name=ds_name,
            worksheet_id=worksheet_id_str,
            filter_class=fil.get("class", "") or fil.get("filter-class", ""),
            expression=expr,
            line=first_sourceline(fil),
        ))
    return out


def _parse_sorts(
    ws_el: etree._Element, *, worksheet_id_str: str = "",
) -> list[WorksheetSortIR]:
    """Walk every `<sort>` element under ``ws_el`` and emit one row per
    sort target. Tableau renders sort direction as ``direction='ascending'``
    on the sort element itself.
    """
    out: list[WorksheetSortIR] = []
    for sort in ws_el.iter("sort"):
        col = sort.get("column", "")
        refs = find_refs(col)
        if not refs:
            continue
        field_name = refs[-1]
        ds_name = refs[0] if len(refs) >= 2 else ""
        out.append(WorksheetSortIR(
            field_name=field_name,
            datasource_name=ds_name,
            worksheet_id=worksheet_id_str,
            direction=sort.get("direction", "ascending"),
            line=first_sourceline(sort),
        ))
    return out


def parse_datasource_filters(
    ds_el: etree._Element, datasource_name: str,
) -> list[WorksheetFilterIR]:
    """Datasource-level filters (the ones inside ``<datasource>/.../filter``).

    The same IR shape is reused — ``worksheet_id`` is left blank so the
    writer routes the resulting edge to TableauDatasource instead of
    TableauWorksheet.
    """
    # Tableau places datasource filters inside an ``<extract>`` or directly
    # as a child of ``<datasource>``. Walk every ``<filter>`` and emit one
    # row per bracketed reference.
    out: list[WorksheetFilterIR] = []
    for fil in ds_el.findall("./filter") + ds_el.findall("./extract/filter"):
        col = fil.get("column", "")
        refs = find_refs(col)
        if not refs:
            continue
        field_name = refs[-1]
        ds_name = refs[0] if len(refs) >= 2 else datasource_name
        expr = etree.tostring(fil, encoding="unicode")[:512]
        out.append(WorksheetFilterIR(
            field_name=field_name,
            datasource_name=ds_name,
            worksheet_id="",
            filter_class=fil.get("class", "") or fil.get("filter-class", ""),
            expression=expr,
            line=first_sourceline(fil),
        ))
    return out
