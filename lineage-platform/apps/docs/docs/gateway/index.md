---
title: Gateway overview
sidebar_label: Gateway overview
---

# Gateway overview

A FastAPI service that aggregates four parsers behind one URL, hosts
read-only Cypher presets for lineage traversal, and owns the
projects-metadata Postgres table.

Lives at [`lineage-platform/apps/gateway/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/lineage-platform/apps/gateway).

## What it routes

| Prefix | Module | Owns |
|---|---|---|
| `/parse/*` | `parse_proxy.py` | Multipart uploads, dispatches to the right parser, persists project-file references. |
| `/files/*` | `files_routes.py` | The `/files` inventory page, per-file source viewer, single + bulk delete. |
| `/graph/*` | `graph_routes.py` | Schema introspection, neighbor expansion, Cypher presets, read-only raw Cypher. |
| `/projects/*` | `projects.py` | CRUD on the Postgres-backed projects table. |
| `/tws/*` | `tws_routes.py` | Operational SQL queries against `tws.schedules` / `tws.jobs`. |
| `/health`, `/version` | root | Health + parser discovery. |

The full list of endpoints — auto-generated from the live
`/openapi.json` at every docs build — is at
[/reference/api-catalogue](/reference/api-catalogue).

## The read-only Cypher guard

`cypher_guard.assert_read_only(query)` is called by
`/graph/query/cypher` before the gateway forwards user-supplied Cypher
to Neo4j. It token-scans the query and refuses any of
`CREATE`, `MERGE`, `DELETE`, `SET`, `REMOVE`, `DROP`, `CALL { ... }` with
write-side procedures, etc. This makes `/graph/query/cypher` safe to
expose to read-only consumers without proxying every imaginable
mutation.

`/graph/query/preset/<name>` is even safer — it only runs cypher checked
into [`presets/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/lineage-platform/apps/gateway/src/lineage_gateway/presets).

## CORS

`CORS_ALLOWED_ORIGINS` env var (default `http://localhost:3000,
http://localhost:3001`) controls who can hit the gateway from the
browser. Set it in the compose file or the EC2 user-data when
deploying.

## See also

- [Endpoints](/gateway/endpoints) — auto-generated per-endpoint detail.
- [Cypher presets](/gateway/presets) — every preset on disk, rendered as a card.
- [API catalogue](/reference/api-catalogue) — the master cross-service table.
