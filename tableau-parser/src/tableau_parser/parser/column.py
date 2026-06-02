"""Build FieldIR records from `<column>` elements of one datasource.

Two surfaces produce FieldIRs:

1. Top-level ``<column>`` children of ``<datasource>`` — calc fields,
   parameter columns (skipped — see datasource.py), and physical-field
   declarations that Tableau persists at the datasource level.
2. ``<columns>/<column>`` children of a ``<relation type='stored-proc'>`` —
   the proc-declared columns. Each one is anchored to the proc's table FQN
   so the lineage walker can reach it from the proc node.
"""

from __future__ import annotations

from lxml import etree

from tableau_parser.models.domain import FieldIR, TableIR
from tableau_parser.utils.brackets import strip_brackets
from tableau_parser.utils.ids import attribute_id_calculated, attribute_id_physical
from tableau_parser.utils.lines import first_sourceline


def parse_columns(
    datasource_el: etree._Element,
    datasource_id_str: str,
    *,
    primary_table_fqn: str | None = None,
) -> list[FieldIR]:
    """If a single physical table is known, physical fields hang off it (FQN-based id).
    Otherwise physical fields fall back to a datasource-scoped id."""
    out: list[FieldIR] = []
    for col in datasource_el.findall("./column"):
        name = strip_brackets(col.get("name", ""))
        if not name:
            continue
        # Resolution-plan §2.2: a ``<column>`` carrying ``param-domain-type``
        # is a parameter, not a regular field. Skip it here so it lands in
        # the parameter list (via ``datasource._parse_parameters``) and the
        # field list never double-counts. Bin-defining columns are the one
        # exception — they use ``param-domain-type='binsize'`` but produce
        # a real :Attribute. ``derived.parse_bins`` handles those upstream.
        pdt = col.get("param-domain-type", "")
        if pdt and pdt != "binsize":
            continue
        calc_el = col.find("./calculation")
        # Improvement-v2 §5 — only ``<calculation class='tableau'>`` is a
        # real formula calc field. Bin and set columns carry ``class='bin'``
        # and ``class='categorical-bin'`` respectively; those are handled
        # by parser/derived.py and must NOT double-count here.
        klass = calc_el.get("class", "tableau") if calc_el is not None else ""
        if calc_el is not None and klass != "tableau":
            continue
        is_calc = calc_el is not None
        formula = calc_el.get("formula", "") if is_calc else ""

        if is_calc:
            fid = attribute_id_calculated(datasource_id=datasource_id_str, field_name=name)
            tfqn = ""
        elif primary_table_fqn:
            fid = attribute_id_physical(table_fqn=primary_table_fqn, column=name)
            tfqn = primary_table_fqn
        else:
            # No single-table attribution available — scope the id to the datasource.
            fid = attribute_id_calculated(datasource_id=datasource_id_str, field_name=name)
            tfqn = ""

        # For calc fields the <calculation> child carries the more precise
        # source line; for physical fields the <column> element itself.
        field_line = first_sourceline(calc_el, col)

        # Step 8 — sub-field metadata. All optional.
        default_agg = col.get("default-aggregation", "")
        ordinal = _parse_int(col.get("ordinal", ""))
        precision = _parse_int(col.get("precision", ""))
        scale = _parse_int(col.get("scale", ""))
        contains_null = _parse_bool(col.get("contains-null", ""))
        value_aliases = _parse_aliases(col)

        out.append(FieldIR(
            id=fid,
            name=name,
            datasource_id=datasource_id_str,
            datatype=col.get("datatype", ""),
            role=col.get("role", ""),
            is_calculated=is_calc,
            formula=formula,
            table_fqn=tfqn,
            line=field_line,
            default_aggregation=default_agg,
            ordinal=ordinal,
            precision=precision,
            scale=scale,
            contains_null=contains_null,
            value_aliases=value_aliases,
        ))
    return out


def parse_proc_columns(
    datasource_el: etree._Element,
    datasource_id_str: str,
    proc_tables: list[TableIR],
) -> list[FieldIR]:
    """Lift physical fields declared inside a stored-proc relation.

    Tableau places these inside the relation itself:

        <relation type='stored-proc' name='usp_x'>
            <actual-name>[dbo].[usp_x]</actual-name>
            <columns>
                <column datatype='integer' name='order_id' ordinal='1' />
                ...
            </columns>
        </relation>

    They never appear as direct ``<column>`` children of ``<datasource>``,
    so the main ``parse_columns`` walker misses them. We resolve each proc
    by name against ``proc_tables`` and anchor its declared columns to
    that table's FQN (same id scheme as physical fields under a table).
    """
    by_proc_name: dict[str, TableIR] = {
        t.name: t for t in proc_tables if t.relation_type == "stored_proc"
    }
    if not by_proc_name:
        return []

    out: list[FieldIR] = []
    # Stored-proc relations can live as direct datasource children or
    # nested under <connection>. Tag normalisation runs before this so we
    # don't need to worry about FCP prefixes.
    proc_rels = (
        datasource_el.findall("./relation[@type='stored-proc']")
        + datasource_el.findall("./connection/relation[@type='stored-proc']")
    )
    for rel in proc_rels:
        proc_name = strip_brackets(rel.get("name", ""))
        table = by_proc_name.get(proc_name)
        if table is None:
            # The relation walker didn't produce a TableIR (e.g. no
            # actual-name and no name) — skip rather than orphan fields.
            continue
        for col in rel.findall("./columns/column"):
            name = strip_brackets(col.get("name", ""))
            if not name:
                continue
            fid = attribute_id_physical(
                table_fqn=table.fully_qualified_name, column=name,
            )
            out.append(FieldIR(
                id=fid,
                name=name,
                datasource_id=datasource_id_str,
                datatype=col.get("datatype", ""),
                role=col.get("role", ""),
                is_calculated=False,
                formula="",
                table_fqn=table.fully_qualified_name,
                line=first_sourceline(col),
                ordinal=_parse_int(col.get("ordinal", "")),
            ))
    return out


def _parse_int(s: str) -> int | None:
    try:
        return int(s) if s else None
    except (TypeError, ValueError):
        return None


def _parse_bool(s: str) -> bool | None:
    if not s:
        return None
    v = s.strip().lower()
    if v in {"true", "yes", "1"}:
        return True
    if v in {"false", "no", "0"}:
        return False
    return None


def _parse_aliases(col: etree._Element) -> dict[str, str]:
    """Read `<aliases>/<alias>` or `<map><bucket>` rename pairs.

    Tableau emits two shapes:
        <aliases><alias key="raw" value="display" /></aliases>
        <map><bucket value="display"><member value="raw" /></bucket></map>
    Both carry the same lineage signal: a value rename.
    """
    out: dict[str, str] = {}
    for al in col.findall("./aliases/alias"):
        k = al.get("key", "")
        v = al.get("value", "")
        if k:
            out[k] = v
    for bucket in col.findall("./map/bucket"):
        display = bucket.get("value", "")
        for member in bucket.findall("./member"):
            raw = member.get("value", "")
            if raw:
                out[raw] = display
    return out
