# lineage-platform

Local orchestration for the multi-parser knowledge-graph system. Brings up Neo4j, Postgres, the parsers, the gateway, and the frontend with one command.

## Quick start

```bash
docker compose up neo4j postgres neo4j-init        # Phase 0 — infra only
docker compose up                                  # full stack
```

- Frontend (Carbon Design System, G100 theme): http://localhost:3000
- Gateway (FastAPI): http://localhost:8000 — OpenAPI at `/docs`
- Tableau parser:  http://localhost:8001/health
- TWS parser:      http://localhost:8002/health
- QlikView parser: http://localhost:8003/health
- Spark parser:    http://localhost:8004/health
- Neo4j browser:   http://localhost:7475 — login `neo4j` / `lineagepass` (Bolt on 7688)
- Postgres:        `psql postgresql://lineage:lineagepass@localhost:5432/lineage`

## Layout

```
lineage-platform/
├── docker-compose.yml            # Full stack definition
├── infra/
│   ├── neo4j/init.cypher         # Sanity init (real constraints come from lineage-contracts)
│   └── postgres/init.sql         # Bootstrap + (Phase 2) TWS schema
├── apps/
│   ├── gateway/                  # FastAPI aggregation API (Phase 4)
│   └── frontend/                 # Next.js + cytoscape.js (Phase 4)
└── deploy/k8s/                   # Helm/manifest examples (later)
```

## Phase status

- [x] Phase 0 — infra (Neo4j + Postgres) up; shared constraints applied
- [x] Phase 1 — tableau-parser wired in
- [x] Phase 2 — tws-parser wired in (Neo4j + Postgres dual-write)
- [x] Phase 3 — qlikview-parser wired in
- [x] Phase 4 — spark-parser wired in
- [x] Phase 5 — gateway + frontend (Next.js + Carbon Design System, cytoscape.js graph viz)
- [ ] Phase 6 — documentation site (separate port)
- [ ] Future — NLP service (sketch only in plan)

## Frontend overview

`apps/frontend` is a Next.js (App Router) + TypeScript application built with
`@carbon/react` (Carbon Design System v11, G100 dark theme) and `cytoscape.js`
for graph visualization. The frontend never speaks Cypher or to individual
parsers — every request goes through the gateway.

Views:

| Route | Purpose |
|---|---|
| `/` | Dashboard — health of gateway, Neo4j, Postgres, and every parser |
| `/explorer` | Graph explorer — filter by label, search by name, click-to-expand |
| `/lineage` | Lineage tracer — upstream / downstream from a chosen node |
| `/tws` | TWS operations — Postgres-backed schedule search with Carbon DataTable |
| `/parse` | Parse a source — dispatches a file to the right parser via `/parse` |

Node colour in the graph maps to source system:
Tableau (blue), QlikView (green), TWS (magenta), Spark (yellow),
shared `:Table`/`:Attribute`/`:Connection` (purple).

## Gateway endpoints (FastAPI on `:8000`)

- `GET  /health` — gateway + downstream-store reachability
- `GET  /version`
- `POST /parse` — proxies to the parser identified by `source_type`
- `GET  /parse/parsers/health` — fan-out probe to every parser
- `GET  /graph/schema` — live labels, relationship types, property keys
- `GET  /graph/nodes?label=&name_like=&limit=&offset=`
- `GET  /graph/node/{id}/neighbors?depth=1..3`
- `GET  /graph/query/presets` — registered preset names
- `POST /graph/query/preset/{name}?node_id=…`
- `POST /graph/query/cypher` — read-only passthrough (write keywords rejected)
- `GET  /tws/jobs?start_time=…&end_time=…&script_path_like=…&workstation=…`

## Gateway tests

```bash
cd apps/gateway
pip install -e ".[dev]"
pytest tests
```

42 tests cover the cypher guard, preset loader, Neo4j payload converter, parse
proxy, and every endpoint with mocked backends. No real Neo4j/Postgres needed.

## Verifying Phase 0

```bash
docker compose up -d neo4j postgres neo4j-init
docker compose logs neo4j-init    # should print "Constraints applied."

# Confirm constraints landed:
docker exec lineage-neo4j cypher-shell -u neo4j -p lineagepass "SHOW CONSTRAINTS"
```
