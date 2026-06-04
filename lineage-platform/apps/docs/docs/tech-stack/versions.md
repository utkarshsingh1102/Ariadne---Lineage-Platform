---
title: Versions
sidebar_label: Versions
---

# Versions

Pin sources are checked into git and are the single source of truth:

| Service | File |
|---|---|
| tableau-parser | [`tableau-parser/pyproject.toml`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/tableau-parser/pyproject.toml) |
| tws-parser | [`tws-parser/pyproject.toml`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/tws-parser/pyproject.toml) |
| qlikview-parser | [`qlikview-parser/pyproject.toml`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/qlikview-parser/pyproject.toml) |
| spark-parser | [`spark-parser/pyproject.toml`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/spark-parser/pyproject.toml) |
| gateway | [`apps/gateway/pyproject.toml`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/lineage-platform/apps/gateway/pyproject.toml) |
| frontend | [`apps/frontend/package.json`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/lineage-platform/apps/frontend/package.json) |
| docs (this site) | [`apps/docs/package.json`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/lineage-platform/apps/docs/package.json) |

## Datastore images

| Image | Tag |
|---|---|
| `neo4j` | `5.20-community` (+ APOC plugin) |
| `postgres` | `16-alpine` |

## Base images

| Service | Builder | Runtime |
|---|---|---|
| Parsers + gateway | `eclipse-temurin:17-jre` (for ANTLR codegen) + `python:3.11-slim` | `python:3.11-slim` |
| Frontend | `node:20-alpine` | `node:20-alpine` (standalone Next.js output) |
| Docs | `node:20-alpine` | `nginx:1.27-alpine` |

## See also

- [Per service](/tech-stack/per-service) — key deps per service.
- [Tech stack](/tech-stack/) — grouped by responsibility.
