# QlikView Parser — Test Suite

Test suite derived from `qlikview-parser-plan.md` §9. Covers easy → complex QlikView script constructs and asserts the contract the parser is *supposed* to honour (per the plan), not just what the current regex prototype does.

## Layout

```
tests/
├── conftest.py                       # Shared pytest fixtures
├── fixtures/                         # Sample .qvs scripts (input)
│   ├── 01_simple_sql_load.qvs        # Easy: 1 connection, 1 SQL LOAD
│   ├── 02_resident_load.qvs          # Easy: in-memory derivation
│   ├── 03_left_join.qvs              # Easy: implicit-target JOIN
│   ├── 04_concatenate.qvs            # Medium: CONCATENATE / NOCONCATENATE
│   ├── 05_file_load.qvs              # Easy: CSV + TXT loads
│   ├── 06_variables_and_includes.qvs # Medium: SET/LET + $(Include=)
│   ├── 07_subroutines.qvs            # Medium: SUB/END SUB/CALL
│   ├── 08_realistic_dashboard.qvs    # Complex: full end-to-end
│   ├── 08_realistic_dashboard.xml    # Companion XML metadata
│   ├── 09_comments_and_edge_cases.qvs# Comment-handling stress test
│   ├── 10_qvd_load.qvs               # QVD file load
│   └── includes/
│       ├── common.qvs                # Pulled in by 06 and 08
│       └── connections.qvs           # Pulled in by 08
├── unit/
│   ├── test_connections.py
│   ├── test_load_statement.py
│   ├── test_sql_block.py
│   ├── test_resident.py
│   ├── test_join.py
│   ├── test_concatenate.py
│   ├── test_variables.py
│   ├── test_subroutines.py
│   ├── test_includes.py
│   ├── test_expressions.py
│   ├── test_comments.py
│   └── test_field_extraction.py
└── integration/
    ├── test_end_to_end.py
    ├── test_neo4j_schema.py
    └── test_cross_parser_merge.py
```

## Install + run

```bash
pip install -r requirements.txt
pip install -r tests/requirements-test.txt

# Run everything except Neo4j integration tests
pytest tests/ -m "not neo4j"

# Run only unit tests
pytest tests/unit/ -v

# Run a single fixture-scoped test
pytest tests/unit/test_load_statement.py::test_simple_sql_load -v

# Run Neo4j integration tests (requires Neo4j on bolt://localhost:7687)
NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=password \
    pytest tests/integration/ -m neo4j -v

# Show xfail tests as reminders of known gaps
pytest tests/ -v --runxfail
```

## How test outcomes map to the work

| Test outcome | Meaning |
|---|---|
| **PASS** | Current code honours this part of the plan. Don't regress it. |
| **XFAIL** (expected failure) | Plan requires it; current code doesn't do it. **This is the TODO list for the rewrite.** |
| **XPASS** (unexpectedly passed) | The xfail marker is stale — remove it. |
| **FAIL** | Regression. A previously-working construct broke. |
| **SKIP** | Environmental gate (e.g. Neo4j not running). |

The `xfail` markers are deliberate — they document what's *supposed* to work per the plan but doesn't yet. As Phase 0–3 in `REVIEW.md §7` land, the developer removes the `xfail` markers and the tests turn green.

## Coverage targets (from plan §9.4)

- Overall: ≥75% line coverage.
- `visitor/ir_visitor.py` and `parser/load_statement.py`: 100%.
- Generated ANTLR code (`src/qlikview_parser/generated/`): **excluded** from coverage.

```bash
pytest tests/ --cov=qlikview_parser --cov-report=term-missing \
    --cov-config=tests/.coveragerc
```

## Fixture provenance

All fixtures are hand-written for testing. None contain real Barclays data. The `08_realistic_dashboard.qvs` is a synthesised "kitchen sink" example designed to exercise every construct simultaneously. When real anonymisable samples become available (see `REVIEW.md §8`), add them as `11_*.qvs`, `12_*.qvs`, etc.

## Markers used

Defined in `pyproject.toml` / `pytest.ini`:

- `@pytest.mark.neo4j` — requires a running Neo4j (`NEO4J_URI` env var).
- `@pytest.mark.slow` — takes >2s; excluded from quick runs.
- `@pytest.mark.xfail(reason="...")` — known gap vs plan, tracked in `REVIEW.md §7`.
