---
title: Spark tests
sidebar_label: Spark tests
---

# Spark tests

95 fixtures across the canonical fixture tree under
[`spark-parser/fixtures/`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/tree/main/spark-parser/fixtures).

## Fixture tree

| Subdir | Exercises |
|---|---|
| `pyspark/` | DataFrame chains, joins, `withColumn`, writes, UDFs, broadcast hints. |
| `sparksql/` | `.sql` files; sqlglot-driven column lineage. |
| `notebooks/` | `.ipynb` + `.py` mixed runs with Jupyter magic stripping. |
| `projects/` | Multi-file projects (`from x import y` cross-file resolution). |
| `orchestration/` | Airflow DAG + Databricks workflow fixtures attaching as `OrchestrationJobIR`. |
| `delta_logs/` | Delta transaction logs for runtime schema-evolution correlation. |
| `event_logs/` | Spark event logs for stage / job metrics. |

## Coverage matrix

Per [`spark-parser/FEATURES.md`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/spark-parser/FEATURES.md):

- **v0.2 PASS** — 206 / 206 internal unit + integration tests.
- **v0.2 frontend contract** — 128 / 128 contract tests.

Recent fixes covered in v0.2 work:

- `spark.createDataFrame(...)` recognition (LHS variable now binds, so
  downstream `.join()` / `.write` chains thread through).
- Jupyter line magics (`!pip install …`) stripped before AST parse.
- Cell magics: `%%sql` → `language_override="sql"`, `%%bash` → body
  dropped, `%%timeit` → body kept as Python.

## See also

- [Parser overview](/parsers/spark).
- [Simulator — join and select](/parsers/spark#simulator--join-and-select).
