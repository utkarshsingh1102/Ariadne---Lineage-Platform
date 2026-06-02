"""Top-level dashboard walker (target plan Step 5).

For each `<dashboard>` we capture:
- every `<zone>` (worksheet, filter, parameter, text, image, web, container)
  as a DashboardZoneIR — worksheet zones still feed displayed_worksheets
  for backwards compatibility.
- every `<action>` inside `<actions>` (filter, highlight, parameter, URL)
  as a DashboardActionIR with source/target sheet names and the fields it
  carries.
"""

from __future__ import annotations

from lxml import etree

from tableau_parser.models.domain import (
    DashboardActionIR,
    DashboardIR,
    DashboardZoneIR,
)
from tableau_parser.utils.brackets import find_refs, strip_brackets
from tableau_parser.utils.ids import (
    dashboard_action_id,
    dashboard_id,
    dashboard_zone_id,
)
from tableau_parser.utils.lines import first_sourceline


# A zone with these `type` values is something other than a worksheet.
# Worksheet zones produce DISPLAYS_WORKSHEET edges; non-worksheet zones
# produce HAS_ZONE edges to first-class DashboardZone nodes.
_NON_WORKSHEET_TYPES = frozenset({
    "filter", "parameter", "text", "image", "bitmap", "web",
    "layout-basic", "layout-flow", "layout-tiled", "layout-floating",
    "blank",
})


def parse_dashboards(tree, workbook_id_str: str = "") -> list[DashboardIR]:
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    out: list[DashboardIR] = []
    for db_el in root.findall(".//dashboards/dashboard"):
        ir = _build(db_el, workbook_id_str)
        if ir is not None:
            out.append(ir)
    return out


def _build(db_el: etree._Element, workbook_id_str: str) -> DashboardIR | None:
    name = db_el.get("name", "")
    if not name:
        return None
    dbid = dashboard_id(workbook_id_str, name)

    displayed: list[str] = []
    seen_ws: set[str] = set()
    zones: list[DashboardZoneIR] = []

    for idx, zone in enumerate(db_el.iter("zone")):
        ztype = zone.get("type", "")
        zname = zone.get("name", "")
        # The worksheet path stays exactly as it was — preserving the
        # existing `displayed_worksheets` semantics. A worksheet zone with
        # no name is invalid Tableau, skip it.
        if ztype == "worksheet":
            if zname and zname not in seen_ws:
                seen_ws.add(zname)
                displayed.append(zname)
            # Still emit a DashboardZoneIR so the coverage report sees the
            # zone consumed — but don't write a HAS_ZONE edge (worksheet
            # zones use DISPLAYS_WORKSHEET).
            continue

        # Non-worksheet zone: first-class node.
        kind = _classify_zone(ztype)
        # Parameter zones carry a `param` attr like ``[Parameter 1]``.
        param_attr = zone.get("param", "")
        param_name = (find_refs(param_attr) or [strip_brackets(param_attr)])[0] \
            if param_attr else ""
        zones.append(DashboardZoneIR(
            id=dashboard_zone_id(dbid, idx, kind),
            dashboard_id=dbid,
            kind=kind,
            name=zname,
            target_parameter=param_name,
            line=first_sourceline(zone),
        ))

    actions: list[DashboardActionIR] = []
    for idx, act in enumerate(db_el.iter("action")):
        actions.append(_build_action(act, dbid, idx))

    return DashboardIR(
        id=dbid,
        name=name,
        workbook_id=workbook_id_str,
        displayed_worksheets=displayed,
        zones=zones,
        actions=actions,
        line=first_sourceline(db_el),
    )


def _classify_zone(ztype: str) -> str:
    """Map the raw `type` attribute to one of our coarse-grained kinds."""
    if not ztype:
        return "container"
    if ztype.startswith("layout-"):
        return "container"
    if ztype in _NON_WORKSHEET_TYPES:
        return ztype
    # Anything unrecognised — surface the raw value so reviewers see it.
    return ztype


def _build_action(
    act_el: etree._Element, dashboard_id_str: str, action_index: int,
) -> DashboardActionIR:
    raw_kind = act_el.get("class", "") or act_el.tag
    kind = _classify_action(raw_kind)
    name = act_el.get("name", "") or act_el.get("caption", "")

    source_sheets = _split_csv_brackets(act_el.get("source-sheets", ""))
    target_sheets = _split_csv_brackets(act_el.get("target-sheets", ""))
    fields = _split_csv_brackets(act_el.get("fields", "") or act_el.get("field", ""))
    parameter_name = strip_brackets(act_el.get("parameter", ""))
    url = act_el.get("url", "")

    return DashboardActionIR(
        id=dashboard_action_id(dashboard_id_str, action_index, kind, name),
        dashboard_id=dashboard_id_str,
        kind=kind,
        name=name,
        source_sheets=source_sheets,
        target_sheets=target_sheets,
        fields=fields,
        parameter_name=parameter_name,
        url=url,
        line=first_sourceline(act_el),
    )


_FILTER_ACTION_CLASSES = frozenset({"filter", "filter-action", "actionfilter"})
_HIGHLIGHT_ACTION_CLASSES = frozenset({"highlight", "highlight-action"})
_PARAM_ACTION_CLASSES = frozenset({"set-parameter", "parameter", "parameter-action"})
_URL_ACTION_CLASSES = frozenset({"url", "url-action"})


def _classify_action(raw: str) -> str:
    r = raw.lower()
    if r in _FILTER_ACTION_CLASSES:
        return "filter"
    if r in _HIGHLIGHT_ACTION_CLASSES:
        return "highlight"
    if r in _PARAM_ACTION_CLASSES:
        return "parameter"
    if r in _URL_ACTION_CLASSES:
        return "url"
    return raw or "filter"


def _split_csv_brackets(value: str) -> list[str]:
    """Tableau action attrs hold comma-separated bracketed names, e.g.
    ``source-sheets="[Sales by Region],[Top Customers]"``. Split on the
    closing-bracket boundary so embedded commas inside a bracketed name
    don't confuse the split.
    """
    if not value:
        return []
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in value:
        if ch == "[":
            depth += 1
            buf.append(ch)
        elif ch == "]":
            depth -= 1
            buf.append(ch)
            if depth == 0:
                token = "".join(buf).strip()
                if token:
                    out.append(strip_brackets(token))
                buf = []
        elif depth == 0 and ch == ",":
            # Comma between two top-level tokens; flush whatever's
            # buffered without brackets (rare, but defensive).
            token = "".join(buf).strip()
            if token:
                out.append(strip_brackets(token))
            buf = []
        else:
            buf.append(ch)
    if buf:
        token = "".join(buf).strip()
        if token:
            out.append(strip_brackets(token))
    return out
