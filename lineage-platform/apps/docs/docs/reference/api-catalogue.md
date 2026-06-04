---
title: API catalogue
sidebar_label: API catalogue
---

# API catalogue

Every endpoint across every service in one searchable table. **43** endpoints across **5** services.

Auto-generated from each service's `/openapi.json` by [`scripts/build-api-ref.mjs`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/lineage-platform/apps/docs/scripts/build-api-ref.mjs).

| Service | Method | Path | Summary |
|---|---|---|---|
| [Gateway](/reference/openapi/gateway) | `GET` | `/files` | List Files |
| [Gateway](/reference/openapi/gateway) | `DELETE` | `/files/{source}/{file_id}` | Delete File |
| [Gateway](/reference/openapi/gateway) | `GET` | `/files/{source}/{file_id}/source` | File Source |
| [Gateway](/reference/openapi/gateway) | `POST` | `/files/bulk-delete` | Bulk Delete Files |
| [Gateway](/reference/openapi/gateway) | `GET` | `/files/summary` | Files Summary |
| [Gateway](/reference/openapi/gateway) | `GET` | `/graph/node/{node_id}/neighbors` | Node Neighbors |
| [Gateway](/reference/openapi/gateway) | `GET` | `/graph/nodes` | List Nodes |
| [Gateway](/reference/openapi/gateway) | `POST` | `/graph/query/cypher` | Cypher Query |
| [Gateway](/reference/openapi/gateway) | `POST` | `/graph/query/preset/{name}` | Run Preset |
| [Gateway](/reference/openapi/gateway) | `GET` | `/graph/query/presets` | List Presets Endpoint |
| [Gateway](/reference/openapi/gateway) | `GET` | `/graph/schema` | Graph Schema |
| [Gateway](/reference/openapi/gateway) | `GET` | `/health` | Health |
| [Gateway](/reference/openapi/gateway) | `POST` | `/parse` | Parse |
| [Gateway](/reference/openapi/gateway) | `GET` | `/parse/parsers/health` | Parser Health |
| [Gateway](/reference/openapi/gateway) | `POST` | `/parse/upload` | Parse Upload |
| [Gateway](/reference/openapi/gateway) | `POST` | `/parse/upload/auto` | Parse Upload Auto |
| [Gateway](/reference/openapi/gateway) | `POST` | `/parse/upload/multi` | Parse Upload Multi |
| [Gateway](/reference/openapi/gateway) | `GET` | `/projects` | List Projects |
| [Gateway](/reference/openapi/gateway) | `POST` | `/projects` | Create Project |
| [Gateway](/reference/openapi/gateway) | `DELETE` | `/projects/{project_id}` | Delete Project |
| [Gateway](/reference/openapi/gateway) | `GET` | `/projects/{project_id}` | Get Project |
| [Gateway](/reference/openapi/gateway) | `GET` | `/tws/jobs` | Jobs |
| [Gateway](/reference/openapi/gateway) | `GET` | `/version` | Version |
| [Tableau parser](/reference/openapi/tableau) | `GET` | `/health` | Health |
| [Tableau parser](/reference/openapi/tableau) | `GET` | `/metrics` | Metrics |
| [Tableau parser](/reference/openapi/tableau) | `POST` | `/parse` | Parse |
| [Tableau parser](/reference/openapi/tableau) | `POST` | `/parse/batch` | Parse Batch |
| [Tableau parser](/reference/openapi/tableau) | `GET` | `/version` | Version |
| [TWS parser](/reference/openapi/tws) | `POST` | `/export/excel` | Export Excel |
| [TWS parser](/reference/openapi/tws) | `GET` | `/health` | Health |
| [TWS parser](/reference/openapi/tws) | `GET` | `/metrics` | Metrics |
| [TWS parser](/reference/openapi/tws) | `POST` | `/parse` | Parse |
| [TWS parser](/reference/openapi/tws) | `POST` | `/parse/batch` | Parse Batch |
| [TWS parser](/reference/openapi/tws) | `POST` | `/parse/multi` | Parse Multi |
| [TWS parser](/reference/openapi/tws) | `GET` | `/version` | Version |
| [QlikView parser](/reference/openapi/qlikview) | `GET` | `/health` | Health |
| [QlikView parser](/reference/openapi/qlikview) | `POST` | `/parse` | Parse |
| [QlikView parser](/reference/openapi/qlikview) | `GET` | `/version` | Version |
| [Spark parser](/reference/openapi/spark) | `GET` | `/health` | Health |
| [Spark parser](/reference/openapi/spark) | `POST` | `/parse` | Parse |
| [Spark parser](/reference/openapi/spark) | `POST` | `/parse/project` | Parse Project Endpoint |
| [Spark parser](/reference/openapi/spark) | `POST` | `/parse/with-runtime` | Parse With Runtime |
| [Spark parser](/reference/openapi/spark) | `GET` | `/version` | Version |

## See also

- [Cypher presets](/reference/presets/lineage-upstream) — the read-only graph queries the gateway exposes.
- [CLI reference](/reference/cli) — `python -m <parser>_parser …` invocations.

