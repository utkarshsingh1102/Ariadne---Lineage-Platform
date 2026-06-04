---
title: Cypher presets
sidebar_label: Cypher presets
---

# Cypher presets

The gateway exposes six checked-in Cypher queries under
`POST /graph/query/preset/<name>?node_id=<id>`. The bodies are read from
[`apps/gateway/src/lineage_gateway/presets/*.cypher`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/lineage-platform/apps/gateway/src/lineage_gateway/presets)
at request time — edit the file, reload, no rebuild needed.

| Preset | Purpose |
|---|---|
| [`lineage-upstream`](/reference/presets/lineage-upstream) | Walks "what produced this" — `CONTAINS_*`, `READS_TABLE`, `DERIVES_FROM`, etc. |
| [`lineage-downstream`](/reference/presets/lineage-downstream) | Walks "what consumes this" — inverse of upstream. |
| [`qlikview-chart-lineage`](/reference/presets/qlikview-chart-lineage) | QlikView-specific chart → datasource → table path. |
| [`spark-connections`](/reference/presets/spark-connections) | All connections a Spark script reads from. |
| [`spark-write-targets`](/reference/presets/spark-write-targets) | All tables a Spark script writes to. |
| [`tableau-physical-tables`](/reference/presets/tableau-physical-tables) | Tableau datasource → physical table walks. |

## Adding a new preset

1. Drop a `.cypher` file under
   `lineage-platform/apps/gateway/src/lineage_gateway/presets/`.
2. The file is read at request time — no gateway rebuild needed.
3. Run `npm run build:api-ref` from `apps/docs/` to regenerate the
   per-preset MDX page so it shows up in this docs site.
