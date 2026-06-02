"""Coverage harness for the XML walk.

After parsing, walk the tree once and surface any element tag that isn't
either (a) on the mapped-tag whitelist — known to be consumed by one of
the parser modules — or (b) on the ignore-tag set (purely-presentational
markup with no lineage value).

Each unmapped element becomes one ``WorkbookIR.warnings`` entry of the
shape ``{type: "unmapped_element", detail: <tag>, line: <sourceline>}``.

The whitelist + ignore-set are intentionally additive — when a new
parser module is wired up (e.g. groups/sets in Step 6, dashboard zones
in Step 5), its tag goes here. Keeping the lists in code (not config)
makes the "did we forget to map something?" question a regular code
review item.
"""

from __future__ import annotations

from lxml import etree


# Tags actively consumed by the parser modules. Anything in this set is
# "covered" — the walkers produce IR for it (or use it as a structural
# container that holds covered children). When you add support for a new
# element family, add its tag here.
_MAPPED_TAGS: frozenset[str] = frozenset({
    # Workbook + datasource structure
    "workbook", "datasources", "datasource",
    "named-connections", "named-connection", "connection",
    # Relation / table tree
    "relation", "clause", "expression",
    # Column / calc / metadata
    "metadata-records", "metadata-record", "column", "calculation",
    "aliases", "alias", "map", "bucket",
    # Resolution-plan §2.4 — leaf children of <metadata-record>. Their
    # *values* are absorbed by the column handler (via parent-name lookup
    # for table attribution) rather than walked as standalone IR. Listing
    # them here keeps the coverage walker from flagging them as unmapped.
    "remote-name", "local-name", "parent-name", "local-type", "remote-type",
    "aggregation", "contains-null", "collation", "padded-semantics",
    "attributes", "attribute",
    # Step 6 — derived fields. ``member`` is the inner list element used
    # by both <group>/<bucket> recoding and <groupfilter> filter sets.
    "group", "groupfilter", "set", "bin", "drill-path", "field", "member",
    # Improvement-v2 §10 — ``<drill-paths>`` is the container for
    # hierarchies; ``parse_hierarchies`` descends into it via iter().
    "drill-paths",
    # Improvement-v2 §9 — worksheet blending (data blending).
    "datasource-relationship",
    # Improvement-v2 §2 — stored-proc relation children.
    "actual-name", "columns", "parameters", "parameter",
    # Worksheets
    "worksheets", "worksheet", "view", "table",
    "rows", "cols", "datasource-dependencies",
    # Worksheet field-role children that the shelf walker consumes
    "filter", "color", "size", "label", "detail", "tooltip", "shape",
    # Step 4 — filters and sorts are first-class IR now
    "filter-class", "groupfilter", "groupfilter-filterexpr",
    "sort", "sort-token", "rank",
    # Dashboards
    "dashboards", "dashboard", "zones", "zone",
    # Step 5 — actions
    "actions", "action",
    # Top-level repository (workbook attrs only — body is metadata)
    "repository-location",
})


# Purely-presentational / repository-chrome tags. No lineage value, so
# excluded from the unmapped report.
_IGNORE_TAGS: frozenset[str] = frozenset({
    # Layout / typography / colour
    "style", "style-rule", "format", "formatted-text", "run", "encoding",
    "layout-options", "layouts", "layout", "size", "padding", "border", "fill",
    "device-layouts", "device-layout",
    # Generic structural chrome
    "preferences", "preference", "user", "users", "manifest",
    "datatypes", "datatype",
    "thumbnails", "thumbnail",
    "window", "windows",
    "panes", "pane", "shelf", "title", "caption",
    "axis", "axes", "mark-encodings", "encodings", "encoding",
    "tabs", "viz",
    # Sort / mark element internals processed implicitly
    "sort", "rank",
    # Improvement-v2 — FCP-prefixed feature flags (post-normalize_tree) plus
    # connection-customization chrome and parameter-domain leaf containers.
    # These carry no lineage value and should not surface as unmapped.
    "document-format-change-manifest",
    "MarkAnimation", "ObjectModelEncapsulateLegacy", "SheetIdentifierTracking",
    "connection-customization", "vendor", "driver",
    "customizations", "customization",
    "members", "range",
    "extract",
})


def unmapped_warnings(tree: etree._ElementTree | etree._Element) -> list[dict]:
    """Return one warning per element with a tag outside _MAPPED_TAGS ∪ _IGNORE_TAGS."""
    root = tree.getroot() if hasattr(tree, "getroot") else tree
    if root is None:
        return []
    out: list[dict] = []
    seen_tags: set[str] = set()  # de-dup so the report stays compact
    for el in root.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if not tag:
            continue
        if tag in _MAPPED_TAGS or tag in _IGNORE_TAGS:
            continue
        # Surface only the first occurrence of each unmapped tag per file
        # — listing every single instance buries the signal in noise.
        if tag in seen_tags:
            continue
        seen_tags.add(tag)
        out.append({
            "type": "unmapped_element",
            "detail": tag,
            "line": getattr(el, "sourceline", None),
        })
    return out
