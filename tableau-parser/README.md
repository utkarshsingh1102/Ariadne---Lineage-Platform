# tableau-parser

Parses Tableau `.twb` / `.twbx` workbooks and writes lineage (workbook → datasource → connection → physical table → column → calculated field → worksheet → dashboard) into the shared Neo4j knowledge graph defined in [`lineage-contracts`](../lineage-contracts/).

Phase 1 of the multi-parser plan ([`../qlikview-parser-plan-md-tableau-parser-humble-chipmunk.md`](../../../.claude/plans/qlikview-parser-plan-md-tableau-parser-humble-chipmunk.md)).

## Quick start (with `lineage-platform/docker-compose.yml`)

```bash
# From the repo root:
docker compose -f lineage-platform/docker-compose.yml up -d neo4j postgres neo4j-init tableau-parser

# Smoke check:
cd tableau-parser
./scripts/smoke.sh
```

Then open Neo4j Browser at <http://localhost:7474> (login `neo4j` / `lineagepass`) and try:

```cypher
MATCH (w:TableauWorkbook)-[:CONTAINS_DATASOURCE]->(d)-[:READS_TABLE]->(t:Table)
RETURN w.name, d.name, t.fully_qualified_name;
```

## Local dev (no Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env   # adjust NEO4J_URI etc. as needed
uvicorn tableau_parser.main:app --reload --port 8001
```

## Layout

```
src/tableau_parser/
├── main.py                    # FastAPI app
├── config.py                  # pydantic-settings
├── api/                       # HTTP surface (routes + Pydantic schemas)
├── extractor/                 # .twbx → .twb, .twb → ElementTree
├── parser/                    # XML → IR  (one module per concern)
│   ├── connection.py
│   ├── relation.py
│   ├── column.py
│   ├── calculation.py
│   ├── datasource.py
│   ├── worksheet.py
│   ├── dashboard.py
│   ├── workbook.py            # orchestrator
│   └── sql_parser.py          # sqlglot wrapper for custom-SQL relations
├── graph/                     # IR → Cypher MERGE
│   ├── client.py
│   ├── queries.py
│   ├── writer.py
│   └── schema.py              # label/rel-type constants
├── models/domain.py           # IR dataclasses
└── utils/                     # ids, brackets, logging
```

## Contract

Consumes [`lineage-contracts`](../lineage-contracts/) version `0.1.0`:

- `:Table` / `:Attribute` / `:Connection` ID rules from `schema/node-id-rules.md`
- Constraints from `schema/neo4j-constraints.cypher` (applied by `lineage-platform`'s `neo4j-init` service)
- HTTP surface conforms to `openapi/parser-api.yaml`

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/parse` | `{file_path, neo4j_database?, overwrite?}` → `{id, stats, duration_ms, warnings}` |
| `POST` | `/parse/batch` | Array of `ParseRequest` |
| `GET` | `/health` | Liveness + Neo4j check |
| `GET` | `/version` | Parser + contract version |
| `GET` | `/metrics` | Prometheus exposition |

## Tests

A separate user-maintained test suite is plugged in here (see top-level `tests/`). The included `tests/fixtures/minimal_single_datasource.twb` is provided only for the manual `scripts/smoke.sh` end-to-end check; the user's own suite drives the formal test runs.
