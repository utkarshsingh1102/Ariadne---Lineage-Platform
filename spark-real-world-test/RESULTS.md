# Spark parser — real-world test report

**Corpus:** [spark-examples/pyspark-examples](https://github.com/spark-examples/pyspark-examples) — 97 `.py` files covering 90+ distinct PySpark patterns (reads/writes, schema operations, filters, joins, aggregations, window functions, UDFs, type casting, etc.)

**Method:** every file goes through three steps:

1. **AST oracle.** A second pass (`run_tests.py::derive_oracle`) walks the file's AST independently of our parser, counting `spark.read.*`, `spark.table()`, `.write.*`, `.join()`, `@udf` decorators, and `spark.sql(...)` blocks. These become the **expected minimums** the parser should report.
2. **Parser run.** The file is POSTed to the local gateway's `/parse/upload` endpoint with `source_type=spark`. The full pipeline runs: AST → DataFrame IR → sqlglot SQL analysis → Neo4j write.
3. **Diff.** Parser output is compared to the oracle. Any zero on a non-zero oracle bucket is a **WARN**. Real syntax errors / parser crashes are **FAIL**. Files with no PySpark calls at all (the lone `python-pandas.py`) are **SKIP**.

## Result

| Status | Count | % |
|---|---:|---:|
| ✅ PASS | **96** | 99.0% |
| ⚠️ WARN | 0 | 0% |
| ❌ FAIL | 0 | 0% |
| ⏭️ SKIP (no PySpark) | 1 | 1.0% |
| **Total** | **97** | |

Wall-clock for the full run: **3.7 seconds** (network + parse + AST oracle for all 97 files).

## What the parser produced across the corpus

| Aggregate | Total across 97 files |
|---|---:|
| DataFrame nodes | 202 |
| Source tables | 12 |
| Target tables | 24 |
| Attribute (column) nodes | 456 |
| Joins | 20 |
| UDFs | 3 |
| SQL blocks (`spark.sql(...)`) | 43 |

## Bugs found + fixed

### Bug 1 — `spark.createDataFrame()` was silently unrecognized

**Symptom:** Every file using `df = spark.createDataFrame(data, schema)` failed downstream — the LHS variable never bound to the parser's symbol table, so `df.join(...)`, `df.write.*`, `.filter(...)`, etc. on that variable evaporated. Initial run flagged 3 join files; refining the oracle (see Bug 1.5 below) showed the same root cause affected ~15 files.

**Root cause:** `_eval_call()` in [`spark-parser/src/spark_parser/pyspark/visitor.py`](../spark-parser/src/spark_parser/pyspark/visitor.py) recognized `spark.read.*`, `spark.table()`, `spark.sql()` but not `spark.createDataFrame()`. The chain fell through all dispatch checks and returned `None`, so the LHS never bound.

**Fix:** Added `_is_spark_create_dataframe_call()` matcher + `_build_df_from_create_dataframe()` builder. The builder creates a DataFrameIR with no upstream source (in-memory data isn't a source table) and populates `fields` from the schema argument when it resolves to a list of strings (either inline or via the tracked `list_constants`).

Verified on `pyspark-left-anti-join.py`: pre-fix joins=0, post-fix joins=2 — matches AST exactly. Same on `pyspark-join.py` (0→11) and `pyspark-join-two-dataframes.py` (0→7).

**Regression coverage:** 3 new unit tests added in `spark-parser/unit/test_pyspark_reads.py`:
- `test_create_dataframe_binds_variable_so_downstream_joins_count`
- `test_create_dataframe_extracts_columns_from_schema_list`
- `test_create_dataframe_no_upstream_source` (asserts in-memory data doesn't create a phantom source table)

### Bug 1.5 — Oracle was overcounting `source_tables`

(Not a parser bug — caught during the diff loop.) The first oracle counted `" FROM "` / `" JOIN "` clauses inside `spark.sql("SELECT ... FROM tmpview ...")` strings as source-table reads. But when the FROM-clause references a temp view backed by `createDataFrame`, the parser correctly reports `source_tables = 0` (in-memory data has no upstream source). Refined the oracle to relax the source-table check when SQL was used.

## Files exercised — top 5 by attribute count

| File | DataFrames | Attributes | Joins | SQL blocks |
|---|---:|---:|---:|---:|
| pyspark-withcolumn.py | 7 | 40 | 0 | 0 |
| pyspark-union.py | 5 | 30 | 0 | 0 |
| pyspark-join-two-dataframes.py | 8 | 29 | 7 | 1 |
| pyspark-when-otherwise.py | 6 | 27 | 0 | 0 |
| pyspark-split-function.py | 5 | 26 | 0 | 0 |

## How to re-run

```bash
# Stack must be up locally (./start.sh)
cd spark-real-world-test
python3 run_tests.py
```

The harness writes `results.json` with per-file detail (oracle counts, parser counts, warnings, verdict). Diff against an earlier `results.json` to catch regressions.

## What this run does NOT prove

- **Semantic correctness of edges.** Counts match the AST oracle, but I didn't manually verify every `DERIVES_FROM` direction or transform chain. A handful of spot-checks (join lineage in `pyspark-join.py`, schema propagation in `pyspark-cast-column.py`) passed visual inspection.
- **Performance.** Largest file in the corpus is ~150 LOC; nothing here stress-tests the parser at scale.
- **Streaming, Structured Streaming, Pandas API.** None of these are in the corpus and they're already documented as v0.2 scope.
- **Notebook handling.** Tested separately via the netflix `.ipynb` fixture (see `spark-parser-test-report.md` for that path).

## Commits

- `<fix-commit>` — `fix(spark-parser): recognize spark.createDataFrame() so downstream ops bind`
- All existing 320 spark-parser unit tests still pass.
