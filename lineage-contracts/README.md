# lineage-contracts

Shared contract for the multi-parser knowledge-graph system. **Every parser repo must consume this** to keep cross-parser lineage stitched together.

## What lives here

| Path | Purpose |
|---|---|
| `schema/neo4j-constraints.cypher` | Idempotent constraints + indexes applied to Neo4j on first boot. Union of all parsers' constraints. |
| `schema/node-id-rules.md` | The canonical recipe for deriving deterministic node IDs. **Source of truth.** |
| `schema/shared-labels.md` | Which labels are shared across parsers and which properties each parser is responsible for. |
| `schema/postgres/tws-schema.sql` | Postgres DDL for the TWS mirror tables. |
| `openapi/parser-api.yaml` | Common HTTP contract every parser exposes (`POST /parse`, `GET /health`, etc.). |
| `openapi/gateway-api.yaml` | Frontend-facing aggregation API exposed by the gateway. |
| `fixtures-index.md` | Catalog of fixture files maintained per parser. |

## How to consume

In each parser repo:

```
git submodule add ../lineage-contracts vendor/lineage-contracts
```

Or pin a tagged version once published as a Python package + npm package.

## Versioning

Semantic versioning. Breaking schema changes (renaming labels, changing ID rules) require a major bump and coordinated re-release of every parser.

Current version: `v0.1.0` (Phase 0 — initial scaffold)
