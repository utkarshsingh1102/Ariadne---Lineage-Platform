---
title: Parse your first workbook
sidebar_label: Parse your first workbook
---

# Parse your first workbook

End-to-end: stand up the platform, upload a Tableau workbook, click
into the lineage graph.

## Prerequisites

- Platform running locally per [Quick start](/overview/quick-start).
- A `.twb` to upload — one of the canonical fixtures from
  [`tableau-parser/tests/fixtures/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/tableau-parser/tests/fixtures)
  works.

## Step 1 — Upload

1. Open `http://localhost:3000/parse`.
2. Drop the `.twb` onto the upload area (or click to browse).
3. Pick **Tableau** as the source type.
4. Click **Parse**.

## Step 2 — Read the result

The result panel populates with:

- **Status** — `ok` / `partial` / `failed`.
- **Stats** — counts of datasources, worksheets, dashboards, attributes.
- **Warnings** — `unresolved_table` and friends.
- **Trace upstream / downstream** buttons — these are your way into the
  lineage graph.

## Step 3 — Trace

Click **Trace upstream**. The lineage page loads with the workbook as
the seed; the cypher walk pulls in datasources, attributes, tables,
and any cross-parser convergence (e.g. an `:Attribute` that's also
exposed via a Spark `:DataFrame`).

## Step 4 — Search

Type a column name (e.g. `customer_id`) into the search bar and press
Enter. The matching attributes get an amber `.highlighted` border. Add
more terms; they cumulate as removable tags.

## See also

- [See the parser work](/tutorials/see-the-parser-work) — what was
  happening inside the parser during that upload.
- [Trace Spark lineage](/tutorials/trace-spark-lineage) — same flow,
  Spark side.
- [Cross-system impact analysis](/tutorials/cross-system-impact-analysis)
  — pulling all four parsers' contributions into one trace.
