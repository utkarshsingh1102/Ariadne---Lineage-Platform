---
title: Lineage trace
sidebar_label: Lineage trace
---

# Lineage trace

The lineage page has two modes, switched by query-string.

## Single-node trace

`/lineage?node_id=<id>&direction=upstream`

Calls `POST /graph/query/preset/lineage-upstream?node_id=<id>` with an
empty body. Returns one `GraphPayload`. The frontend renders the
returned nodes + edges.

## Combined trace

`/lineage?node_ids=<id1>,<id2>,<id3>`

Calls **both** `lineage-upstream` and `lineage-downstream` for **each**
id in parallel, then unions the results client-side (deduping by node
id and edge id). Powers the "Lineage: whole project" button on the
Projects page.

## What the presets walk

The chains include `CONTAINS_SCHEDULE`, `CONTAINS_JOB`,
`CONTAINS_DATAFRAME`, `CONTAINS_DATASOURCE`, `CONTAINS_WORKSHEET`,
`CONTAINS_DASHBOARD`, `READS_TABLE`, `WRITES_TABLE`, `DERIVES_FROM`,
`DERIVES_FROM_DATAFRAME`, `EXECUTES`, `HAS_ATTRIBUTE`, `HAS_COLUMN`,
plus a handful of parser-specific relationships. The bodies live in
[`apps/gateway/src/lineage_gateway/presets/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/lineage-platform/apps/gateway/src/lineage_gateway/presets) and the cards in [/gateway/presets](/gateway/presets).

## Search + highlight

Type a term and press Enter — the term becomes a removable tag below
the search bar and every node whose label/name matches gets an amber
`.highlighted` border. Add more terms; they cumulate. Click a tag's ×
to remove.

## See also

- [Cypher presets](/gateway/presets).
- [Trace Spark lineage](/tutorials/trace-spark-lineage) — full walk-through.
- [Cross-system impact analysis](/tutorials/cross-system-impact-analysis).
