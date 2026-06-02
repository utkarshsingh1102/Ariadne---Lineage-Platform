# TWS (Tivoli Workload Scheduler) Parser — Test Suite

Standalone test suite derived from `tws-parser-plan.md`. Mirrors the parser's expected module layout (`parser/`, `visitor/`, `graph/`, `rdbms/`) and covers **both** input formats (composer-text DSL via ANTLR, XML export via lxml) plus **both** persistence layers (Neo4j and Postgres).

This suite is **contract-first**: the TWS parser does not yet exist in this repo. Tests target the imports declared in the plan (e.g. `from tws_parser.parser.dependencies import resolve`). If the module isn't importable, the suite skips cleanly with a single explanatory message — so the developer can run `pytest` from day one and watch tests come online as code lands.

## Layout

```
tws-parser-tests/
├── README.md
├── pytest.ini
├── conftest.py                       # Module-presence skip + Neo4j + Postgres gates
├── requirements-test.txt
├── .gitignore
├── fixtures/
│   ├── README.md
│   ├── 01_single_schedule_single_job.txt
│   ├── 02_multi_job_with_follows.txt
│   ├── 03_schedule_level_dependency.txt
│   ├── 04_resource_and_file_deps.txt
│   ├── 05_complex_run_cycles.txt
│   ├── 06_realistic_dump_many_schedules.txt
│   ├── 07_xml_export_single.xml
│   └── 08_xml_export_full.xml
├── unit/
│   ├── test_format_detector.py      (composer-text vs XML)
│   ├── test_grammar.py              (ANTLR grammar — parse-tree shape)
│   ├── test_visitor.py              (ANTLR Visitor → ScheduleIR)
│   ├── test_xml_parser.py           (lxml path → same ScheduleIR shape)
│   ├── test_schedule.py             (Schedule IR builder)
│   ├── test_job.py                  (Job IR builder)
│   ├── test_dependencies.py         (FOLLOWS / NEEDS / OPENS resolution)
│   ├── test_run_cycle.py            (run-cycle normalisation + cron)
│   ├── test_script_resolver.py      (SCRIPTNAME → script type/args split)
│   └── test_ids.py                  (deterministic IDs, plan §5.4)
└── integration/
    ├── test_end_to_end_neo4j.py     (full pipeline → Neo4j schema contract)
    ├── test_end_to_end_postgres.py  (full pipeline → Postgres tables/views)
    ├── test_api.py                  (FastAPI: /parse, /export/excel, /health)
    └── test_cross_parser_merge.py   (:Script merges with Ab Initio parser)
```

## Install + run

```bash
pip install -r requirements-test.txt

# Day 1 — nothing implemented yet:
pytest -v
# Expected: every test skips with "tws_parser module not importable"

# Run just composer-text (ANTLR) grammar tests:
pytest unit/test_grammar.py unit/test_visitor.py -v

# Run just XML path tests (no ANTLR needed):
pytest unit/test_xml_parser.py -v

# Run Postgres integration (needs a Postgres + alembic migrations applied):
POSTGRES_HOST=localhost POSTGRES_PORT=5432 \
POSTGRES_DB=lineage POSTGRES_USER=tws POSTGRES_PASSWORD=tws \
    pytest integration/test_end_to_end_postgres.py -m postgres -v

# Run Neo4j integration:
NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=password \
    pytest integration/test_end_to_end_neo4j.py -m neo4j -v
```

## How outcomes map to work

| Outcome | What to do |
|---|---|
| **SKIPPED** (module not importable) | Implement the module per `tws-parser-plan.md`, then re-run. |
| **PASS** | Contract met — protect it. |
| **FAIL** | Implementation diverges from the plan. Read the cited plan section. |
| **XFAIL** | Documented gap vs plan (stretch goal). |

## Markers (registered in `pytest.ini`)

- `@pytest.mark.neo4j` — needs a Neo4j (`NEO4J_*` env vars).
- `@pytest.mark.postgres` — needs a Postgres (`POSTGRES_*` env vars + migrations applied).
- `@pytest.mark.slow` — takes more than ~2 s.

## Coverage target (plan §10.6)

- Overall ≥ 80% line coverage.
- 100% on `visitor/ir_visitor.py`, `parser/dependencies.py`, `rdbms/writer.py`.
- Exclude `src/tws_parser/generated/` (ANTLR-generated code).

```bash
pytest --cov=tws_parser --cov-report=term-missing
```

## Fixture provenance

Six composer-text fixtures (`01..06`) plus two XML fixtures (`07`, `08`) covering the same constructs. The realistic fixture `06_realistic_dump_many_schedules.txt` includes the multi-stream / multi-job-per-stream scenario explicitly called out in the meeting (plan §11). Real anonymised TWS dumps should land as `09_*` / `10_*` when available.
