"""Derived-field walkers — groups, sets, bins, hierarchies (plan §6).

These XML element families each define a new logical field that derives
from one or more source fields:

- ``<group>``      — bucket recoding (city → region)
- ``<set>``        — value-list filter
- ``<bin>``        — numeric binning of a measure
- ``<drill-path>`` — ordered field hierarchy

Each becomes a first-class node in Neo4j with a ``DERIVES_FROM`` edge to
each source field. Hierarchies use ``HAS_LEVEL`` since their "derivation"
is ordered membership, not transformation.
"""

from __future__ import annotations

from lxml import etree

from tableau_parser.models.domain import (
    BinIR,
    GroupIR,
    HierarchyIR,
    SetIR,
)
from tableau_parser.utils.brackets import find_refs, strip_brackets
from tableau_parser.utils.ids import bin_id, group_id, hierarchy_id, set_id
from tableau_parser.utils.lines import first_sourceline


def parse_groups(ds_el: etree._Element, datasource_id_str: str) -> list[GroupIR]:
    """Walk every `<group>` child. Groups carry an attr like
    ``column="[City]"`` naming the source field, plus child ``<bucket>``
    or ``<groupfilter>`` elements with the bucket-membership detail."""
    out: list[GroupIR] = []
    for el in ds_el.iter("group"):
        name = strip_brackets(el.get("name", ""))
        if not name:
            continue
        source = el.get("column", "")
        source_names = find_refs(source) or ([strip_brackets(source)] if source else [])
        out.append(GroupIR(
            id=group_id(datasource_id_str, name),
            name=name,
            datasource_id=datasource_id_str,
            source_field_names=[n for n in source_names if n],
            line=first_sourceline(el),
        ))
    return out


def parse_sets(ds_el: etree._Element, datasource_id_str: str) -> list[SetIR]:
    """Walk every `<set>` child. Sets are like groups but carry a single
    membership predicate; we capture the raw expression for review.

    Improvement-v2 §5 also recognises ``<column><calculation
    class='categorical-bin'>`` — Tableau emits sets defined on a
    calculated field via that shape rather than as a top-level ``<set>``.
    """
    out: list[SetIR] = []
    seen: set[str] = set()
    for el in ds_el.iter("set"):
        name = strip_brackets(el.get("name", ""))
        if not name:
            continue
        source = el.get("column", "")
        source_names = find_refs(source) or ([strip_brackets(source)] if source else [])
        # The condition can be in either an attr or a child <expression>.
        condition = el.get("expression", "")
        if not condition:
            expr_el = el.find("./expression")
            if expr_el is not None:
                condition = (expr_el.text or "").strip()
        sid = set_id(datasource_id_str, name)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(SetIR(
            id=sid,
            name=name,
            datasource_id=datasource_id_str,
            source_field_names=[n for n in source_names if n],
            condition_expr=condition,
            line=first_sourceline(el),
        ))

    # ``<column><calculation class='categorical-bin' column='[X]'>`` —
    # the column attr names the source field; the membership list lives
    # in inner ``<bin value='Y'/>`` children which we serialise as the
    # condition expression for human review.
    for col in ds_el.findall("./column"):
        calc_el = col.find("./calculation")
        if calc_el is None or calc_el.get("class", "") != "categorical-bin":
            continue
        name = strip_brackets(col.get("name", ""))
        if not name:
            continue
        source = calc_el.get("column", "")
        source_names = find_refs(source) or ([strip_brackets(source)] if source else [])
        # Serialise the inner <bin value='...'> list as the membership
        # condition. Cheap, stable, human-readable.
        bin_values = [
            b.get("value", "") for b in calc_el.findall("./bin")
            if b.get("value", "")
        ]
        condition = (
            f"members({','.join(bin_values)})" if bin_values else ""
        )
        sid = set_id(datasource_id_str, name)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(SetIR(
            id=sid,
            name=name,
            datasource_id=datasource_id_str,
            source_field_names=[n for n in source_names if n],
            condition_expr=condition,
            line=first_sourceline(calc_el, col),
        ))
    return out


def parse_bins(ds_el: etree._Element, datasource_id_str: str) -> list[BinIR]:
    """Walk every `<column>` that defines a numeric bin. Three shapes
    Tableau uses, in priority order:

    1. ``<column><bin column='[X]' size='100' /></column>`` — explicit
       child element with the source and size as attrs.
    2. ``<column param-domain-type='binsize'>`` — a parameter column that
       represents a bin-size knob.
    3. ``<column><calculation class='bin' formula='[X]' /></column>`` —
       Improvement-v2 §5. The bin's source is parsed from the formula;
       size may come from a separate parameter referenced via
       ``size-parameter``.
    """
    out: list[BinIR] = []
    seen: set[str] = set()
    for col in ds_el.findall("./column"):
        bin_el = col.find("./bin")
        calc_el = col.find("./calculation")
        is_calc_bin = calc_el is not None and calc_el.get("class", "") == "bin"
        is_binsize = col.get("param-domain-type") == "binsize"
        if bin_el is None and not is_calc_bin and not is_binsize:
            continue
        name = strip_brackets(col.get("name", ""))
        if not name:
            continue
        # Source field discovery follows the shape we matched on.
        if bin_el is not None:
            source = bin_el.get("column", "")
            size = bin_el.get("size", "")
        elif is_calc_bin:
            # The formula holds the source field reference.
            source = calc_el.get("formula", "")
            size = calc_el.get("size-parameter", "") or calc_el.get("size", "")
        else:  # is_binsize
            source = col.get("column", "")
            size = col.get("value", "")
        source_names = find_refs(source) or ([strip_brackets(source)] if source else [])
        bid = bin_id(datasource_id_str, name)
        if bid in seen:
            continue
        seen.add(bid)
        out.append(BinIR(
            id=bid,
            name=name,
            datasource_id=datasource_id_str,
            source_field_names=[n for n in source_names if n],
            size=size,
            line=first_sourceline(bin_el, calc_el, col),
        ))
    return out


def parse_hierarchies(
    ds_el: etree._Element, datasource_id_str: str,
) -> list[HierarchyIR]:
    """Walk every `<drill-path>` child. Each carries an ordered list of
    `<field>` (or bracketed string) children naming its levels."""
    out: list[HierarchyIR] = []
    for el in ds_el.iter("drill-path"):
        name = strip_brackets(el.get("name", ""))
        if not name:
            continue
        levels: list[str] = []
        for child in el.iter("field"):
            text = (child.text or "").strip()
            ref = strip_brackets(text)
            if ref:
                levels.append(ref)
        out.append(HierarchyIR(
            id=hierarchy_id(datasource_id_str, name),
            name=name,
            datasource_id=datasource_id_str,
            levels=levels,
            line=first_sourceline(el),
        ))
    return out
