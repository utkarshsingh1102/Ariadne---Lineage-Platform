# Tableau Parser — Test Suite

Standalone test suite derived from `tableau-parser-plan.md`. Mirrors the parser's expected module layout (`extractor/`, `parser/`, `graph/`, `models/`) so each test file maps 1:1 to the production module it covers.

This suite is **contract-first**: the Tableau parser does not yet exist in this repo. Tests target the imports declared in the plan (e.g. `from tableau_parser.parser.calculation import resolve_dependencies`). If the module isn't importable, the suite skips cleanly with a single explanatory message — so the developer can run `pytest` from day one and watch tests come online as code lands.

## Layout

```
tableau-parser-tests/
├── README.md
├── pytest.ini
├── conftest.py                       # Module-presence skip + shared fixtures
├── requirements-test.txt
├── .gitignore
├── fixtures/
│   ├── README.md
│   ├── 01_simple_single_datasource.twb
│   ├── 02_calculated_fields.twb
│   ├── 03_federated_join.twb
│   ├── 04_custom_sql.twb
│   ├── 05_dashboard_with_multiple_sheets.twb
│   ├── 06_packaged_workbook_source.twb   # zipped to .twbx by make_twbx.py
│   ├── 07_parameters.twb
│   ├── 08_realistic_dashboard.twb        # kitchen sink
│   └── make_twbx.py                      # builds 06_packaged_workbook.twbx
├── unit/
│   ├── test_archive.py        (.twbx → .twb extraction)
│   ├── test_xml_loader.py     (.twb → lxml ElementTree)
│   ├── test_brackets.py       (utility: strip [bracketed] identifiers)
│   ├── test_ids.py            (deterministic SHA-256 ID derivation)
│   ├── test_connection.py     (<connection> blocks → :Connection nodes)
│   ├── test_datasource.py     (<datasource> orchestration, federated)
│   ├── test_relation.py       (<relation> types: table / join / text)
│   ├── test_calculation.py    (calculated-field formula → field deps)
│   ├── test_worksheet.py      (<worksheet> + datasource-dependencies)
│   ├── test_dashboard.py      (<dashboard> + zone references)
│   └── test_sql_parser.py     (sqlglot wrapper for custom SQL)
└── integration/
    ├── test_end_to_end.py             (parse fixture → IR shape)
    ├── test_api.py                    (FastAPI TestClient)
    ├── test_neo4j_schema.py           (plan §5 schema contract)
    └── test_cross_parser_merge.py     (Ab Initio / Teradata table merge)
```

## Install + run

```bash
# From this directory
pip install -r requirements-test.txt

# Day 1 (no parser code yet) — everything skips:
pytest -v
# Expected: all skipped with message "tableau_parser module not importable"

# Run only unit tests
pytest unit/ -v

# Run only integration tests (Neo4j gate auto-skips if env not set)
pytest integration/ -v

# Once a Neo4j instance is available:
NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=password \
    pytest integration/ -m neo4j -v
```

## How outcomes map to work

| Outcome | What to do |
|---|---|
| **SKIPPED** (module not importable) | Implement the module per `tableau-parser-plan.md`. |
| **PASS** | Contract met — keep it green. |
| **FAIL** | Implementation diverges from the plan. Read the test's assertion and the plan section it cites. |
| **XFAIL** | Documented gap vs plan (stretch goal or known-deferred). |

## Markers (registered in `pytest.ini`)

- `@pytest.mark.neo4j` — needs a Neo4j instance (`NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`).
- `@pytest.mark.slow` — takes more than ~2 s.

## Coverage target (plan §9.4)

- Overall ≥ 80% line coverage.
- 100% on `parser/calculation.py` and `parser/relation.py`.
- Exclude `tests/`, generated code, and `__init__.py` files.

```bash
pytest --cov=tableau_parser --cov-report=term-missing
```

## Fixture provenance

All fixtures are hand-written, minimised XML — no real Tableau workbooks. Each fixture exercises one specific construct from `tableau-parser-plan.md §2` plus the kitchen-sink `08_realistic_dashboard.twb` that combines everything. When real `.twb`/`.twbx` workbooks are sourced (plan §10), add them as `09_*`, `10_*`, etc.
