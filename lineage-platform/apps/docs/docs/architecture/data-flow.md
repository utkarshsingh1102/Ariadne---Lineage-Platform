---
title: Data flow
sidebar_label: Data flow
---

# Data flow

What happens between a file upload and a rendered lineage graph.

```mermaid
sequenceDiagram
  autonumber
  actor User
  participant FE as Frontend :3000
  participant GW as Gateway :8000
  participant P as Parser :800x
  participant N as Neo4j
  participant PG as Postgres

  User->>FE: pick file in /parse
  FE->>GW: POST /parse/upload
  GW->>P: POST /parse (proxied)
  P->>P: lex → parse → IR → resolve
  P->>N: MERGE batches
  alt parser is TWS
    P->>PG: INSERT INTO tws.schedules / tws.jobs
  end
  P-->>GW: { parsed_node_ids, stats, warnings }
  GW->>PG: INSERT INTO project_files (if project upload)
  GW-->>FE: ParseResponse
  FE->>FE: show Trace upstream / downstream buttons
  User->>FE: click Trace
  FE->>GW: POST /graph/query/preset/lineage-upstream?node_id=…
  GW->>N: run cypher preset (read-only)
  N-->>GW: nodes + edges
  GW-->>FE: GraphPayload
  FE->>FE: Cytoscape renders subgraph
```

## Why proxy through the gateway

- **Single host:port for the frontend** — the four parser ports are an
  implementation detail; the frontend always talks to `:8000`.
- **CORS + auth in one place** — only the gateway exposes
  `CORS_ALLOWED_ORIGINS`; parsers stay internal to the docker network.
- **Read-only Cypher guard** — the gateway's `/graph/query/cypher` runs
  user-supplied Cypher through `cypher_guard.assert_read_only` before
  forwarding to Neo4j, so the public-facing surface can never mutate
  the graph.
- **File provenance** — `/parse/upload` saves to a shared `uploads/`
  volume (mounted into both gateway and parser containers) so the parser
  opens the same path the gateway wrote.

## See also

- [System architecture](/architecture/system).
- [Frontend lineage trace](/frontend/lineage-trace).
