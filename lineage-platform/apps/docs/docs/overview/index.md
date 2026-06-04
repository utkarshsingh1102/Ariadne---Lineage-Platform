---
title: Welcome to Ariadne
sidebar_label: Welcome
slug: /
---

# Welcome to Ariadne

**Ariadne is a unified data-lineage knowledge graph.** It ingests
artifacts from four different systems — Tableau workbooks, IBM TWS
schedules, QlikView apps, Spark scripts — and consolidates them into a
single Neo4j graph where you can trace a column read by a Spark
notebook back to the dashboard that consumes it, the TWS job that
triggers the notebook, and the source table they all share.

## What's in the box

- **Four parser microservices** — one per source system, each a
  FastAPI app that writes deterministic MERGE batches into Neo4j.
- **A gateway** — proxies parse requests, hosts read-only Cypher
  presets for lineage traversal, owns the Postgres-backed projects
  table.
- **A Next.js + Carbon frontend** — Cytoscape-based graph explorer
  with upstream / downstream tracing, file inventory, project view,
  TWS runtime-window search.
- **Cross-parser shared labels** — `:Table`, `:Connection`,
  `:Attribute`, `:Script`. Two parsers that compute the same
  canonical id for one of these collapse onto the same node, which
  is how cross-system lineage actually works.

## Who this is for

- **Data engineers** debugging "why did this table change" or
  building impact analysis tooling.
- **Data architects** evaluating Ariadne as a lineage platform.
- **Internal team** maintaining or extending the parsers.

## Where to go next

| You want to… | Start here |
|---|---|
| Stand it up locally | [Quick start](/overview/quick-start) |
| Understand the architecture | [System architecture](/architecture/system) |
| See the parsers in action | [See the parser work](/tutorials/see-the-parser-work) |
| Browse every API | [API catalogue](/reference/api-catalogue) |
| Deploy to AWS | [Deploy → AWS](/deploy/aws) |
| Know what dependencies ship | [Tech stack](/tech-stack/) |

## The master plan

This documentation site is **Phase 6** of a six-phase plan tracked in
`lineage-platform/README.md`. Phases 0–5 shipped the infrastructure,
the four parsers, the gateway, and the frontend. Phase 6 — what you're
reading — consolidates the scattered READMEs, plan docs and diagrams
into one canonical destination.
