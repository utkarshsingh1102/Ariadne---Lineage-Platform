---
title: Cross-system impact analysis
sidebar_label: Cross-system impact analysis
---

# Cross-system impact analysis

A Tableau dashboard breaks. You suspect upstream changes. What pipeline
or notebook touched it last?

## The scenario

- A `:Dashboard` is rendering stale data.
- It consumes `:Datasource` X.
- Datasource X reads `:Table` `analytics.orders`.
- `analytics.orders` is **shared** — multiple parsers have written
  edges to it.

## The trace

1. **Find the dashboard's id.** `GET /files?source=tableau`, locate the
   workbook, drill into its dashboards.
2. **Trace upstream from the dashboard.**
   `POST /graph/query/preset/lineage-upstream?node_id=<dashboard_id>`.
3. **Spot the shared `:Table`.** Look for purple nodes (shared label)
   in the returned subgraph — these are where lineage crosses parser
   boundaries.
4. **Pivot through the table.** Trace upstream from `analytics.orders`
   to find every writer: TWS jobs (`EXECUTES` a script that writes),
   Spark `:DataFrame`s with `WRITES_TABLE` edges, anything else that
   produces the table.
5. **Recently changed?** Each node carries `last_seen_at` — pick the
   ones bumped most recently.

## Why this works at all

Because every parser computed the **same SHA-256 id** for the shared
`:Table`. See [Cross-parser convergence](/parsers/convergence) and
[Determinism](/architecture/determinism).

## See also

- [Trace Spark lineage](/tutorials/trace-spark-lineage).
- [Cypher presets](/gateway/presets) — the queries you'd use directly
  in Neo4j Browser.
