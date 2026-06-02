# tws-parser

Parses IBM Tivoli Workload Scheduler (TWS) schedule + job definitions and writes:

- **Neo4j** — `:Schedule → :Job → :Script` lineage (matches [`lineage-contracts`](../lineage-contracts/))
- **Postgres** — operational mirror (`tws.schedules`, `tws.jobs`, `tws.v_runtime_window`, …) for fast SQL-driven discovery ("what runs in the 05:30–06:30 window?")

Phase 2 of the multi-parser plan. Supports two input formats:
- **composer-text** (`SCHEDULE … END` DSL) — parsed via ANTLR 4
- **XML export** (`<scheduleDefinitions>…`) — parsed via lxml

Both paths converge on the same `ScheduleIR` so downstream writers don't care which one was used.

## Prerequisites

- Python 3.11+
- Java 11+ (only for `make grammar` codegen — runtime container does **not** need Java)
- Neo4j 5.x + Postgres 16 (the `lineage-platform` docker-compose brings both up)

## Quick start (with `lineage-platform/docker-compose.yml`)

```bash
# From the repo root:
docker compose -f lineage-platform/docker-compose.yml up -d \
    neo4j postgres neo4j-init tws-parser

# Smoke check (parses minimal_daily.txt + minimal_xml.xml):
cd tws-parser
./scripts/smoke.sh
```

Then poke around:

```bash
# 05:30–06:30 window query (Postgres mirror)
PGPASSWORD=lineagepass psql -h localhost -U lineage -d lineage -c \
  "SELECT job_name, schedule_name, script_path, start_time
   FROM tws.v_runtime_window
   WHERE start_time BETWEEN '05:30' AND '06:30';"
```

```cypher
// Neo4j: chain a Job to whatever Script it calls
MATCH (s:Schedule)-[:CONTAINS_JOB]->(j:Job)-[:CALLS_SCRIPT]->(sc:Script)
RETURN s.name, j.name, sc.path, sc.script_type;
```

## Local dev (no Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
make grammar              # regenerates src/tws_parser/generated/ from grammar/*.g4
cp .env.example .env      # adjust NEO4J_URI / POSTGRES_HOST if needed
make migrate              # alembic upgrade head — creates tws schema in Postgres
uvicorn tws_parser.main:app --reload --port 8002
```

## Layout

```
tws-parser/
├── grammar/                                 # ANTLR grammar (source of truth)
│   ├── TWSComposerLexer.g4
│   └── TWSComposerParser.g4
├── tools/antlr-4.13.1-complete.jar          # Vendored ANTLR jar
├── src/tws_parser/
│   ├── main.py                              # FastAPI entrypoint
│   ├── config.py                            # pydantic-settings
│   ├── api/                                 # routes + Pydantic schemas + Excel export
│   ├── generated/                           # ANTLR output (GITIGNORED)
│   ├── visitor/
│   │   ├── ir_visitor.py                    # parse-tree → ScheduleIR
│   │   └── error_listener.py
│   ├── parser/
│   │   ├── orchestrator.py                  # format-aware entrypoint
│   │   ├── format_detector.py               # xml vs composer-text
│   │   ├── composer.py                      # ANTLR-backed composer-text path
│   │   ├── xml_export.py                    # lxml-backed XML path
│   │   ├── run_cycle.py                     # EVERY_WEEKDAY → cron, etc.
│   │   └── script_resolver.py               # /path/run.sh + args → typed Script
│   ├── graph/                               # Neo4j writer (class-based GraphWriter)
│   ├── rdbms/                               # SQLAlchemy + Alembic + PostgresWriter
│   └── utils/                               # ids, logging, time_windows
├── tests/
│   └── fixtures/
│       ├── minimal_daily.txt                # composer-text smoke fixture
│       └── minimal_xml.xml                  # XML export smoke fixture
└── scripts/
    └── smoke.sh                             # End-to-end: /health, /version, /parse, SQL query
```

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/parse` | `{input_path, format?, neo4j_database?, write_neo4j?, write_postgres?, overwrite?}` |
| `POST` | `/parse/batch` | Array of `ParseRequest` |
| `POST` | `/export/excel` | Filter the Postgres mirror, stream back as `.xlsx` |
| `GET` | `/health` | Reports both Neo4j and Postgres connectivity |
| `GET` | `/version` | Parser + contract version |
| `GET` | `/metrics` | Prometheus exposition |

## Grammar workflow

Generated lexer/parser/visitor live in `src/tws_parser/generated/` and are **gitignored**. They regenerate automatically:

- Local: `make grammar` (Java 11+ on PATH)
- Docker build: stage 1 of `Dockerfile` (Java only here; the runtime image is Java-free)

Edit `grammar/*.g4`, run `make grammar`, run the suite.

## Tests

A separate user-maintained test suite is plugged in here (see top-level `tests/`). The included fixtures (`minimal_daily.txt`, `minimal_xml.xml`) are for the `scripts/smoke.sh` manual end-to-end check; the user's own suite drives the formal test runs.

## Contract

Consumes [`lineage-contracts`](../lineage-contracts/) `v0.1.0`:

- Node IDs: `schedule::workstation::scheduler::name`, `job::schedule_id::name`, `script::<lowercased_path>`
- The `:Script` node is the **cross-parser anchor** — when a TWS job's `SCRIPTNAME` matches a path the Ab Initio / BTEQ parser writes, both land on the same node
- Postgres DDL lives in `lineage-contracts/schema/postgres/tws-schema.sql`; the Alembic baseline migration applies it verbatim
