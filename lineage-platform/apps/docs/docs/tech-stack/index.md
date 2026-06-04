---
title: Tech stack
sidebar_label: Tech stack
---

# Tech stack

Every dependency the platform ships with, grouped by responsibility.

## Languages

| Language | Where | Why |
|---|---|---|
| **Python 3.11** | All four parsers, gateway | Mature data-engineering ecosystem; ANTLR / lxml / sqlglot all first-class. |
| **TypeScript 5** | Frontend, docs site | Strict types across the React tree; same toolchain for two apps. |
| **JavaScript (ESM)** | Build scripts (`build-api-ref.mjs`, etc.) | Node's `fetch` makes the auto-gen pipeline a one-file script. |

## Parsing

| Library | Used by | Role |
|---|---|---|
| **ANTLR4 4.13** | TWS, QlikView | Lexer + parser generated from `.g4` grammars. Built at container-image time. |
| **lxml** | Tableau | DOM walker for `.twb` XML. |
| **Python `ast`** | Spark | Walks PySpark DataFrame chains in `.py` / `.ipynb`. |
| **sqlglot 26** | Tableau, QlikView, Spark | SQL parser + dialect translator; powers embedded-SQL lineage. |
| **olefile** | QlikView | Opens `.qvw` OLE compound containers. |
| **sqlite3** (stdlib) | QlikView | Reads `.qvf` Qlik Sense containers. |

## Backend

| Library | Role |
|---|---|
| **FastAPI** | Every parser + the gateway. OpenAPI is the source of truth for `/reference/api-catalogue`. |
| **Pydantic v2** | Request/response models, validation. |
| **neo4j (Python driver) 5.x** | Bolt client to Neo4j. Used by all parsers + gateway. |
| **asyncpg** | Postgres pool in the gateway (projects, TWS runtime-window queries). |
| **openpyxl** | TWS Excel export. |
| **structlog** | JSON-structured logs across all services. |

## Frontend

| Library | Role |
|---|---|
| **Next.js 14** | App Router, standalone output for the Docker image. |
| **React 18** | UI. |
| **Carbon Design System 11** | `@carbon/react`, `@carbon/icons-react`, `@carbon/styles`. Component library + tokens. |
| **Cytoscape.js 3** | Graph rendering. Layered layout via `cytoscape-elk`. |

## Datastores

| Service | Image | Role |
|---|---|---|
| **Neo4j 5.20 Community** | `neo4j:5.20-community` + APOC | Knowledge graph. Constraints from `lineage-contracts/`. |
| **PostgreSQL 16** | `postgres:16-alpine` | TWS mirror tables, projects metadata. |

## Test

| Library | Role |
|---|---|
| **pytest 7** | Unit + integration tests across every parser. |
| **pytest-asyncio** | Async gateway tests. |
| **testcontainers-python** | Spins ephemeral Neo4j for integration tests. |
| **hypothesis** | Property-based fuzzing in selected parsers. |

## Container & orchestration

| | |
|---|---|
| **Docker + docker-compose v2** | All eight services. YAML anchor `*restart_policy` keeps everything on `restart: unless-stopped`. |
| **Multi-stage Dockerfiles** | Slim runtime images (parsers: codegen → install; docs: node → nginx). |

## AWS deploy

| | |
|---|---|
| **Terraform AWS provider** | One-EC2 deploy template in `deploy/aws/`. |
| **EC2 Amazon Linux 2023** | `t3.medium` default. |
| **EBS gp3** | 30 GB root volume. |
| **Elastic IP** | Stable IP across stop/start. |

## Docs site (this one)

| | |
|---|---|
| **Docusaurus 3** | React + MDX, built-in versioning. |
| **Mermaid** | Diagrams. |
| **@easyops-cn/docusaurus-search-local** | Offline-first search until Algolia DocSearch is approved. |
| **prism-react-renderer** | Syntax highlighting. |

## See also

- [Per service](/tech-stack/per-service) — same data sliced per service.
- [Versions](/tech-stack/versions) — pin matrix from every `pyproject.toml` and `package.json`.
