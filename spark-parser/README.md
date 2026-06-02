# Spark Parser — Test Suite

Standalone, contract-first test suite derived from `spark-parser-plan.md`. Covers all three input shapes: **PySpark** scripts (`.py`), **Spark SQL** files (`.sql`), and **notebooks** (`.ipynb` + Databricks `.py`).

The Spark parser does not yet exist in this repo. Tests target the imports declared in the plan (e.g. `from spark_parser.pyspark.visitor import parse_pyspark`). If `spark_parser` is not importable, every test skips cleanly with a single explanatory message — so the developer can run `pytest` from day one and watch tests come online as code lands.

## Layout

```
spark-parser-tests/
├── README.md
├── pytest.ini
├── conftest.py
├── requirements-test.txt
├── .gitignore
├── fixtures/
│   ├── README.md
│   ├── pyspark/
│   │   ├── 01_simple_read_write.py
│   │   ├── 02_join_and_select.py
│   │   ├── 03_with_column_chain.py
│   │   ├── 04_groupby_agg.py
│   │   ├── 05_union.py
│   │   ├── 06_udf_usage.py
│   │   ├── 07_dynamic_table_name.py      # partial-lineage flag test
│   │   ├── 08_spark_sql_inside.py        # spark.sql("...") inside Python
│   │   └── 09_realistic_etl.py           # kitchen sink
│   ├── sparksql/
│   │   ├── 01_simple_ctas.sql
│   │   ├── 02_insert_overwrite.sql
│   │   ├── 03_merge_into.sql
│   │   ├── 04_cte_chain.sql
│   │   └── 05_partition_write.sql
│   └── notebooks/
│       ├── 01_simple.ipynb
│       ├── 02_databricks_format.py       # # Databricks notebook source
│       └── 03_mixed_python_sql.ipynb
├── unit/
│   ├── test_format_detector.py           (.py/.sql/.ipynb/.dbc/.scala detection)
│   ├── test_notebook.py                  (Jupyter + Databricks notebook extraction)
│   ├── test_path_parser.py               (s3://, abfss://, gs:// → structured info)
│   ├── test_ids.py                       (deterministic SHA-256 ID rules)
│   ├── test_pyspark_reads.py             (spark.read.*, spark.table, spark.sql)
│   ├── test_pyspark_writes.py            (saveAsTable, save, insertInto)
│   ├── test_pyspark_transformations.py   (select / withColumn / drop / filter)
│   ├── test_pyspark_joins.py             (inner/left/right/outer/cross)
│   ├── test_pyspark_variables.py         (reassignment, branches, loops)
│   ├── test_pyspark_udfs.py              (@udf, @pandas_udf detection)
│   └── test_sparksql_lineage.py          (CTAS / INSERT / MERGE / CTE / window)
└── integration/
    ├── test_end_to_end.py                (every fixture → IR shape)
    ├── test_api.py                       (FastAPI: /parse, /health, /version)
    ├── test_neo4j_schema.py              (plan §5 schema contract)
    └── test_cross_parser_merge.py        (Tableau/Teradata table merge)
```

## Install + run

```bash
pip install -r requirements-test.txt

# Day 1 — nothing implemented yet, all skip:
pytest -v

# Run only PySpark unit tests:
pytest unit/test_pyspark_*.py -v

# Run only Spark SQL unit tests:
pytest unit/test_sparksql_lineage.py -v

# Run only notebook tests:
pytest unit/test_notebook.py -v

# Run Neo4j integration (requires a running Neo4j):
NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=password \
    pytest integration/ -m neo4j -v
```

## How outcomes map to work

| Outcome | What to do |
|---|---|
| **SKIPPED** (module not importable) | Implement the module per `spark-parser-plan.md`. |
| **PASS** | Contract met — keep it green. |
| **FAIL** | Implementation diverges from plan. Read the cited section. |
| **XFAIL** | Documented scope gap (e.g. Scala out of scope for v0.1). |

## Markers

- `@pytest.mark.neo4j` — needs Neo4j (`NEO4J_*` env vars).
- `@pytest.mark.slow` — takes more than ~2 s.

## Coverage target (plan §9.5)

- Overall ≥ 80 % line coverage.
- 100 % on `pyspark/visitor.py`, `pyspark/transformations.py`, `sparksql/lineage.py`.

```bash
pytest --cov=spark_parser --cov-report=term-missing
```

## Scope reminder (plan §2.4 + §14)

The Spark parser is **v0.1** with a deliberately bounded scope:

- **Scala Spark** — out of scope, asserted as XFAIL.
- **Structured Streaming** — captured but no streaming-specific modelling.
- **UDF body introspection** — only inputs/outputs at call site.
- **Dynamic table names** — best-effort resolution, otherwise `lineage_partial=true` + warning.

These boundaries are explicitly tested so the developer doesn't accidentally over-scope.
