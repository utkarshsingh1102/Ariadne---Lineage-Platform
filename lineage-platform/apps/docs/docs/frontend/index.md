---
title: Frontend overview
sidebar_label: Frontend overview
---

# Frontend overview

A Next.js 14 app (App Router, standalone output) using IBM Carbon
Design System for layout / components / tokens and Cytoscape.js for
graph rendering.

Lives at [`lineage-platform/apps/frontend/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/lineage-platform/apps/frontend).

## Page routes

| Route | Purpose |
|---|---|
| `/` | Dashboard — gateway / Neo4j / Postgres / parser health |
| `/parse` | Upload one or many files; routes via `/parse/upload/auto` |
| `/files` | Per-source inventory of parsed files; bulk delete; project view |
| `/lineage` | Upstream / downstream tracer from one node OR combined lineage from N nodes |
| `/explorer` | Label/name filter, click-to-expand graph |
| `/projects` | Project CRUD + per-project file list |
| `/tws` | Postgres-backed runtime-window search across TWS jobs |

## Key components

| Component | Role |
|---|---|
| `GraphCanvas.tsx` | Cytoscape host; manages layout, focused/highlighted classes |
| `GraphToolbar.tsx` | Collapsible search/filter strip with cumulative search tags |
| `GraphZoomControls.tsx` | Floating zoom −/⊡/+ above the minimap |
| `SourceCodePanel.tsx` | Syntax-highlighted file viewer (Prism) |
| `MultiSelectFooter.tsx` | Bulk-action footer used by `/files` |
| `AppShell.tsx` | Carbon shell — header, side nav, theme switcher |

## State

The frontend is mostly stateless — every page fetches from the gateway
on mount. Two exceptions:

1. **Lineage page** — keeps a cumulative search-query list locally so
   typing "orders" then "customers" highlights both sets without
   reloading the cypher.
2. **Files page** — keeps a per-source checked-id set so bulk delete
   knows what to send.

## See also

- [Pages tour](/frontend/pages) — screenshots of every page.
- [Cytoscape styling](/frontend/cytoscape) — how node colours + edge
  labels are configured.
- [Lineage trace](/frontend/lineage-trace) — single-node vs combined
  `node_ids=` lineage in detail.
