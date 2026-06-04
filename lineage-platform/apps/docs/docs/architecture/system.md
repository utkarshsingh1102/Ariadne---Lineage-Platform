---
title: System architecture
sidebar_label: System architecture
---

# System architecture

Eight services, three datastores, one knowledge graph.

```mermaid
flowchart LR
  subgraph Inputs["Inputs"]
    twb[".twb / .twbx"]
    qvs[".qvs / .qvw / .qvf"]
    tws[".txt composer / .xml"]
    spark[".py / .sql / .ipynb"]
  end

  subgraph Parsers["Parser services (FastAPI)"]
    P1["tableau-parser :8001"]
    P2["tws-parser :8002"]
    P3["qlikview-parser :8003"]
    P4["spark-parser :8004"]
  end

  subgraph Platform["Platform"]
    GW["gateway :8000"]
    FE["frontend :3000"]
    DOCS["docs :3002"]
  end

  subgraph Stores["Datastores"]
    NEO["Neo4j 5.20<br/>:7475 / :7688"]
    PG["Postgres 16<br/>:5432"]
  end

  twb --> P1
  qvs --> P3
  tws --> P2
  spark --> P4

  P1 -->|MERGE| NEO
  P2 -->|MERGE| NEO
  P3 -->|MERGE| NEO
  P4 -->|MERGE| NEO
  P2 -->|mirror| PG

  GW -->|proxy /parse| P1
  GW -->|proxy /parse| P2
  GW -->|proxy /parse| P3
  GW -->|proxy /parse| P4
  GW -->|read| NEO
  GW -->|read| PG
  FE -->|API| GW
  DOCS -->|/openapi.json at build time| GW
```

## Services in detail

| Service | Image / build | Port | Purpose |
|---|---|---|---|
| **neo4j** | `neo4j:5.20-community` + APOC | 7475 (HTTP), 7688 (Bolt) | Knowledge graph store. Auth `neo4j` / `lineagepass`. |
| **postgres** | `postgres:16-alpine` | 5432 | TWS mirror tables (`tws.schedules`, `tws.jobs`) + `projects` metadata. |
| **tableau-parser** | `tableau-parser/Dockerfile` (Python) | 8001 | Parses `.twb` / `.twbx`. |
| **tws-parser** | `tws-parser/Dockerfile` (Python + ANTLR codegen) | 8002 | Parses TWS composer DSL + XML exports. Dual-writes to Postgres. |
| **qlikview-parser** | `qlikview-parser/Dockerfile` (Python + ANTLR codegen) | 8003 | Parses `.qvs` / `.qvw` / `.qvf`. |
| **spark-parser** | `spark-parser/Dockerfile` (Python) | 8004 | Parses `.py` / `.sql` / `.ipynb` via AST + sqlglot. |
| **gateway** | `apps/gateway/Dockerfile` (FastAPI) | 8000 | Federates `/parse`, hosts Cypher presets, owns projects. |
| **frontend** | `apps/frontend/Dockerfile` (Next.js + Carbon) | 3000 | Carbon Design System UI with Cytoscape.js graph viz. |
| **docs** | `apps/docs/Dockerfile` (Docusaurus 3 → nginx) | 3002 | This site. |

## Service discovery

All inter-service communication happens over the `lineage-platform_default`
docker network using the compose service names as DNS — `gateway`,
`tableau-parser`, etc. The gateway resolves each parser by its
`PARSER_<NAME>_URL` env var (default: `http://<service>-parser:8000`).

## See also

- [Data flow](/architecture/data-flow) — what happens when a file is uploaded.
- [Storage](/architecture/storage) — what each datastore is responsible for.
- [Determinism](/architecture/determinism) — the SHA-256 id contract.
