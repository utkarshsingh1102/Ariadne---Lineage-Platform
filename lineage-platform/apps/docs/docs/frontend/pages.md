---
title: Pages tour
sidebar_label: Pages tour
---

# Pages tour

A screenshot tour through every page in the frontend.

## Dashboard — `/`

Service health at a glance — gateway, Neo4j, Postgres, four parsers.

![Dashboard](/img/screenshots/01-dashboard.png)

## Parse — `/parse`

Drop a file, pick the source, watch the parse result panel populate.

![Parse a Tableau workbook](/img/screenshots/02-parse-tableau.png)

## Parse result detail

After parsing, the result panel shows stats, warnings, and a **Trace
upstream / downstream** action that jumps into the lineage tracer.

![Parse result detail](/img/screenshots/03-files-tableau-detail.png)

## TWS runtime window — `/tws`

Operational view of TWS jobs by clock window. Backed by the
`tws.v_runtime_window` Postgres view.

![TWS runtime window](/img/screenshots/04-tws-runtime-window.png)

## Spark parse result

The same parse-result shape on a Spark script.

![Spark parse result](/img/screenshots/06-parse-spark-result.png)

## Lineage — `/lineage`

Click a node to expand its neighbourhood. Search filters cumulate as
removable tags.

![Lineage trace from a Spark node](/img/screenshots/07-lineage-star-spark.png)

![Lineage highlighting](/img/screenshots/08-lineage-highlight.png)

## Column-level detail

Click an `:Attribute` to see the columns and the derives_from chain.

![Table with column attributes](/img/screenshots/09-lineage-table-columns.png)

## See also

- [Cytoscape styling](/frontend/cytoscape) — how nodes / edges are coloured.
- [Lineage trace](/frontend/lineage-trace) — single-node vs combined `node_ids=` lineage.
