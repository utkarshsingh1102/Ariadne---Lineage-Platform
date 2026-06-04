---
title: Cytoscape styling
sidebar_label: Cytoscape styling
---

# Cytoscape styling

Source: [`apps/frontend/app/_lib/cytoscape-config.ts`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/lineage-platform/apps/frontend/app/_lib/cytoscape-config.ts).

The docs site's [`CytoscapeMini`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/lineage-platform/apps/docs/src/components/CytoscapeMini.tsx) component mirrors the same palette so embedded
widgets look consistent with screenshots.

## Source-system colours

Every node carries a `source_system` property; the renderer picks a
fill colour from a fixed palette:

| System | Colour chip | Hex |
|---|---|---|
| Tableau | <span className="source-tag source-tag--tableau">Tableau</span> | `#1f6fbf` |
| QlikView | <span className="source-tag source-tag--qlikview">QlikView</span> | `#009844` |
| TWS | <span className="source-tag source-tag--tws">TWS</span> | `#d12771` |
| Spark | <span className="source-tag source-tag--spark">Spark</span> | `#e67e22` |
| Shared label | <span className="source-tag source-tag--shared">Shared</span> | `#8a3ffc` |

Shared labels (`:Table`, `:Connection`, `:Attribute`, `:Script`) are
purple regardless of which parser wrote them.

## Node states

- **default** — standard fill, no border.
- **`.focused`** — red border. The node the user is currently inspecting.
- **`.highlighted`** — amber border. Matched by a cumulative search term.

Both states stack: a node can be focused AND highlighted, and the two
borders compose visually.

## Edge labels

Edge labels are shown rotated along the edge path. Long labels are
truncated by Cytoscape's text-overflow rules; hover for the full value
in the node-details panel.

## Layout

The frontend uses **ELK layered** by default for top-down lineage
flows; the dashboard view uses **concentric** for hub-spoke
visualisation. Both are loaded via `cytoscape-elk` and
`cytoscape/cose`.

## See also

- [Pages tour](/frontend/pages) — Cytoscape in action.
- [Lineage trace](/frontend/lineage-trace) — what the cypher walks return.
