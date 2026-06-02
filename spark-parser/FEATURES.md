# spark-parser v0.2 — Complete Feature List

**Version:** 0.2.0
**Pass rate:** 206 / 206 internal tests + 128 / 128 frontend contract tests (100%)
**Skipped:** 9 Neo4j-gated tests (auto-activate with `testcontainers[neo4j]` + Docker)
**Documented xfail:** 1 (two-hop nested cross-module inlining)

This document is the canonical list of every feature delivered through v0.1 + the five v0.2 phases (Cross-file resolution, Notebook semantics, Schema evolution, Orchestration/Connectors/Abstractions, Federation/Runtime). Items new in v0.2 are marked accordingly inline.

---

## 1. Input formats

- PySpark scripts (`.py`) — plain Python files
- Spark SQL files (`.sql`) — parsed via sqlglot, `dialect="spark"`
- Jupyter notebooks (`.ipynb`) — JSON via nbformat
- Databricks Python notebooks (`.py` with `# Databricks notebook source` header and `# COMMAND ----------` cell separators)
- Databricks archives (`.dbc`) — ZIP-wrapped notebook JSON
- Scala (`.scala`) — graceful refusal with `parser_type=unsupported`
- Encoding auto-detection (UTF-8 default, chardet fallback)
- File-size guard (`MAX_FILE_SIZE_MB`)

---

## 2. PySpark DataFrame API

### 2.1 Reads
- `spark.read.format("X").load(...)` for parquet, delta, csv, json, orc, avro, text, jdbc
- `spark.read.parquet(path)` and the per-format shortcuts
- `spark.table("db.schema.name")` with default-database fallback (`DEFAULT_DATABASE` env)
- `spark.sql("SELECT …")` — extracted SQL handed to sqlglot
- JDBC options (`url`, `dbtable`, `query`)
- Path schemes: `s3://`, `abfss://`, `gs://`, `file://`, plus all connector schemes

### 2.2 Writes
- `df.write.format("X").saveAsTable("...")`
- `df.write.format("X").save("path")`
- `df.write.insertInto("...")`
- Modes (`overwrite`, `append`, `ignore`, `error`)
- Format + mode + options walked back through chains

### 2.3 Transformations
- `select` (positional + alias)
- `selectExpr`
- `withColumn` (including nested-struct paths `a.b.c`)
- `withColumnRenamed` with full rename-chain history (`a → b → c`)
- `drop`
- `filter` / `where`
- `cache` / `persist(level)` / `checkpoint(eager)`
- `repartition(n, *cols)` / `coalesce(n)` with partition_count + columns
- `hint("broadcast" | "shuffle" | "merge")`
- `dropDuplicates`, `distinct`, `sort`, `orderBy`, `limit`

### 2.4 Joins
- `df.join(other, on, how)` for inner / left / right / outer / cross
- `df.crossJoin(other)`
- `broadcast(other)` wrapper detection
- Join broadcast hint propagates to JoinIR + DataFrameIR

### 2.5 Aggregations & set ops
- `groupBy(...).agg(...)` with carry-through of grouping columns
- `union` / `unionAll` / `unionByName` with parent-DataFrame tracking

### 2.6 UDFs
- `@udf` and `@pandas_udf` decorator detection
- `var = udf(fn, ReturnType())` factory form
- Return-type + input-type canonicalisation (`StringType()` → `string`)
- `USES_UDF` edges from derived columns to UDFs

### 2.7 Column-expression resolution
- `col("name")`, `lit(value)`, `col("a") + col("b")`
- `F.when(...).otherwise(...)`
- `cast(t)` and `col.cast(t)` with full `type_history` on the attribute
- `F.expr("a + b * 2")` — string handed to sqlglot for column extraction

### 2.8 Temp views
- `df.createOrReplaceTempView("name")` registers a temp view
- Subsequent `spark.sql("SELECT * FROM name")` resolves through the temp view

---

## 3. Variable + control-flow handling

- Variable reassignment with `creation_order` audit (`df = df.filter(...)`)
- Anonymous method-chain DataFrames (`__anon_<order>`)
- If/else branches — both arms emit lineage; conditional flagging
- For/while loops with literal-list unrolling (no warning when iterable is a list literal)
- Local function calls — inline-walk the function body with arg bindings
- External (unresolved) function calls — `via="external_function"` + carry-through of parent reads

---

## 4. Cross-file resolution *(v0.2 §1)*

- `ProjectIR` aggregates the entry script + every first-party module reachable through imports
- Absolute imports (`import pkg.sub.mod`)
- Relative imports (`from .util import x`, `from ..pkg import y`)
- Package `__init__.py` handling
- Third-party imports (pyspark, os, ...) tracked but not recursed
- One-hop cross-module function inlining
- Cycle-safe recursion (visited set, bounded `max_depth`)
- Import-edge graph (`:IMPORTS` between SparkScripts)
- Configurable extra search paths (`src/` layouts)

---

## 5. Runtime-evaluated logic detection *(v0.2 §3)*

Each emits `WarningIR(type="runtime_dynamic", subtype=…)` plus `lineage_partial=True`:

- `eval()` / `exec()` of runtime strings
- `setattr(obj, name, df)` with non-constant attribute name
- `locals()[name] = …` / `globals()[name] = …`
- `getattr(obj, name)` with non-constant attribute (reflection)
- f-string / `.format()` SQL templates with unresolved interpolations
- For-loops over non-literal iterables

---

## 6. Notebook runtime semantics *(v0.2 §2)*

- Per-cell IR (`NotebookCellIR` with `index`, `language`, `source`, `execution_count`)
- Cell-language detection (Python / SQL / Scala / Markdown)
- `cell_index` carried on each DataFrameIR
- Hidden-state warning when Jupyter cells were executed out of source order
- `%run` magic line detection (both Jupyter and Databricks `# MAGIC %run` forms)
- `dbutils.notebook.run("path", ...)` detection
- `NotebookRunEdgeIR` records source_cell_index → target notebook
- Notebook dependency graph reconstruction

---

## 7. Advanced Spark SQL grammar *(v0.2 §4)*

- CTAS (`CREATE TABLE … AS SELECT …`)
- `INSERT INTO` / `INSERT OVERWRITE` (with partition spec)
- `MERGE INTO … USING … ON …` (WHEN MATCHED UPDATE, WHEN NOT MATCHED INSERT)
- CTEs (`WITH … SELECT …`)
- **Recursive CTEs** — `WITH RECURSIVE` → self-loop derivations with `via="recursive_cte"`
- **LATERAL VIEW** + **LATERAL VIEW OUTER** — per-output-column derivations with the explode expression as source
- **Correlated subqueries** — outer-column references emit `via="correlated_subquery"`
- **Scalar subqueries** — `via="scalar_subquery"` (subquery in expression position)
- **Nested subquery propagation**
- **Window functions** — `via="window"` for ROW_NUMBER, RANK, SUM OVER, named WINDOW clauses, RANGE BETWEEN, INTERVAL frames
- `UNION ALL`
- Source/target table extraction (CTE aliases excluded from sources)

---

## 8. Schema evolution *(v0.2 §5)*

- **Delta `_delta_log` reader** — diffs consecutive `metaData.schemaString` commits
- Event types: `add_column`, `drop_column`, `type_change`, `nullability_change`
- Per-event metadata: `version`, `timestamp_ms`, `from_type` / `to_type`, `from_nullable` / `to_nullable`
- Column **rename propagation** through chains and reassignments (full history on `DataFrameIR.renames`)
- Nested struct **path tracking** (`address.city`, `profile.contact.email_domain`)
- Type evolution lineage via `AttributeIR.type_history` (records each cast's from→to)
- **Column shadowing** detection — `select_alias_duplicate` + `withColumn_overwrite` warnings

---

## 9. Enterprise runtime semantics *(v0.2 §6)*

First-class fields on `DataFrameIR` that survive chain steps and reassignments:

- `cached` (set by `.cache()`)
- `persist_level` (set by `.persist(level)` — captures `MEMORY_ONLY`, etc.)
- `checkpointed`
- `partition_count` (from `.repartition(n)` / `.coalesce(n)`)
- `partition_columns` (from `.repartition(n, *cols)`)
- `broadcast_hint` (from `broadcast(other)` and `.hint("broadcast")`, propagated to JoinIR + the join result)
- AQE metadata from event log (Catalyst rule applications)

---

## 10. Procedural abstractions *(v0.2 §8)*

- Class hierarchy walker (collects every `ClassDef`, methods, base classes)
- `proc = ClassName()` binding tracked in `instance_types`
- `proc.method(df)` inlines the method body (drops `self`/`cls`, binds remaining params)
- MRO walk for inherited methods (same-file bases)
- Higher-order factory inlining: `xf = make_xform(); df2 = xf(df)` — outer-function-returns-inner-FunctionDef pattern
- `df.transform(local_fn)` inlines local + external functions
- `df.foreach`, `df.foreachPartition`, `df.mapPartitions` — captures callback edge; warns + flags partial when callable is external

---

## 11. External-ecosystem connectors *(v0.2 §9)*

Each produces a canonical `:Table` FQN so cross-parser merging works:

| Connector | Format string(s) | Canonical FQN |
|---|---|---|
| Kafka | `kafka` | `kafka://<servers>/<topic>` |
| Iceberg | `iceberg` | `<catalog>.<namespace>.<table>` (Hive-FQN compatible) |
| Hudi | `hudi`, `org.apache.hudi` | Path-based FQN |
| Snowflake | `snowflake`, `net.snowflake.spark.snowflake` | `<sfDatabase>.<sfSchema>.<dbtable>` |
| BigQuery | `bigquery`, `com.google.cloud.spark.bigquery` | `<project>.<dataset>.<table>` |
| Redshift | `redshift`, `io.github.spark_redshift_community.spark.redshift`, `com.databricks.spark.redshift` | JDBC URL + dbtable |

---

## 12. Orchestration layer parsing *(v0.2 §7)*

- **Airflow DAG parser** — `DAG(dag_id, schedule)`, operators: `SparkSubmitOperator`, `BashOperator`, `PythonOperator`, `DatabricksRunNowOperator`, `DatabricksSubmitRunOperator`. Dependencies from `>>`, `<<`, `set_upstream`, `set_downstream`, `chain(...)`.
- **Databricks workflow JSON parser** — Jobs API 2.1 schema. Task types: notebook, spark_python, spark_jar, python_wheel, dbt, sql, pipeline.
- **spark-submit shell parser** — extracts `--conf k=v`, `--master`, `--deploy-mode`, `--py-files`, `--name`, plus full positional argv. Joins shell line continuations.
- New IR: `OrchestrationJobIR`, `OrchestrationTaskIR`, `TaskEdgeIR`.

---

## 13. Neo4j graph persistence

### 13.1 Node labels

| Label | Purpose | Notes |
|---|---|---|
| `:SparkScript` | One per parsed file | Shared `source_system='spark'` |
| `:DataFrame` | Intermediate / named DataFrames | |
| `:Table` | Source / target tables | Shared label with sibling parsers |
| `:Attribute` | Column-level | |
| `:UDF` | Python UDFs | |
| `:Project` *(v0.2)* | Aggregates SparkScripts from `/parse/project` | |

### 13.2 Relationships

| Type | Endpoints | Carries |
|---|---|---|
| `CONTAINS_DATAFRAME` | SparkScript → DataFrame | `creation_order` |
| `READS_TABLE` | DataFrame → Table | `via` |
| `WRITES_TABLE` | DataFrame → Table | `mode`, `via` |
| `HAS_COLUMN` | Table → Attribute | |
| `HAS_FIELD` | DataFrame → Attribute | |
| `DERIVES_FROM` | Attribute → Attribute | `formula`, `via` |
| `DERIVES_FROM_DATAFRAME` | DataFrame → DataFrame | `via` |
| `JOINS_WITH` | DataFrame → DataFrame | `join_type`, `join_condition`, `broadcast_hint` |
| `USES_UDF` | Attribute → UDF | |
| `:IMPORTS` *(v0.2)* | SparkScript → SparkScript | `kind`, `symbol`, `module`, `line` |
| `CONTAINS_SCRIPT` *(v0.2)* | Project → SparkScript | |

### 13.3 Other graph features

- Deterministic SHA-256 IDs (cross-parser merging)
- Idempotent re-parse (zero net diff on second run)
- Uniqueness constraints + indexes per plan §5.3
- Cross-parser FQN merge helpers (`canonical_table_id`, `shared_table_ids`)
- testcontainers Neo4j fallback (v0.2 — auto-activates with Docker)

---

## 14. External catalog integration *(v0.2 §10)*

- **OpenLineage 1.0.5 emitter** — `RUN_START` / `RUN_COMPLETE` events, dataset facets (storage + columnLineage), sourceCode job facet, deterministic run_ids
- Per-project event emission (one event per module)
- **Unity Catalog read-only client** — `GET /api/2.1/unity-catalog/tables/{full_name}` with injectable HTTP transport; emits `unity_catalog_mismatch` warnings

---

## 15. Runtime Spark execution validation *(v0.2 §11)*

- **Spark event-log reader** (JSON-lines) — parses:
  - `SparkListenerSQLExecutionStart` / `…End` (executionId, description, physicalPlan, analyzedPlan, optimizedPlan, duration)
  - `SparkListenerJobStart` / `JobEnd` (job → stages, sqlExecutionId link)
  - `SparkListenerStageSubmitted` / `StageCompleted` (parent IDs, num_tasks, completion time)
- **Catalyst optimization decisions** — rule extraction (PushDownPredicate, ColumnPruning, …) into `OptimizationDecisionIR`
- **Plan correlator** — matches static `SparkScriptIR` DataFrames to runtime SQL executions (SQL-block first, write-ordered second)
- **DAG signatures** — sha256 of canonical edge list (static + runtime); emits `runtime_dag_divergence` warning on mismatch
- **Spark UI REST client** — `/api/v1/applications/{id}/{stages,jobs,sql}` for live History Server mode
- New IR: `RuntimeIR`, `SqlExecutionIR`, `RuntimeJobIR`, `RuntimeStageIR`, `OptimizationDecisionIR`, `RuntimePlanIR`

---

## 16. HTTP API

| Endpoint | Purpose |
|---|---|
| `POST /parse` | Single-file parse |
| `POST /parse/project` *(v0.2)* | Multi-file project parse (entry + project_root) |
| `POST /parse/with-runtime` *(v0.2)* | Script + Spark event-log → correlated IR |
| `GET /health` | Includes Neo4j connectivity state |
| `GET /version` | Parser + version metadata |

Per-parse response: `script_id`, `stats`, `warnings`, `graph` write counts.

---

## 17. Configuration

| Env var | Default | Purpose |
|---|---|---|
| `NEO4J_URI` | – | Bolt connection string |
| `NEO4J_USER` | `neo4j` | |
| `NEO4J_PASSWORD` | – | Required for live writes |
| `NEO4J_DATABASE` | `neo4j` | |
| `NEO4J_DISABLE_TESTCONTAINERS` | unset | Opt-out of the testcontainer fallback |
| `DEFAULT_DATABASE` | `default` | Hive default DB shorthand for `spark.table("X")` |
| `MAX_FILE_SIZE_MB` | `50` | Rejects larger files |
| `LOG_LEVEL` | `INFO` | |
| `BATCH_SIZE` | `1000` | Cypher UNWIND batch size |
| `STRICT_PARSING` | `false` | Fail on any parse warning instead of degrading |

---

## 18. Determinism + reliability

- SHA-256-only IDs (no clock, no random, no `PYTHONHASHSEED`)
- All sortable collections sorted before hashing
- 3× re-parse byte-stable IDs
- `parsed_at` is a property, never part of an ID
- Anonymous DataFrame names use creation order (`__anon_<N>`)

### Graceful failure modes

- `syntax_error` for invalid Python
- `sql_parse_error` for malformed SQL (empty graph + warning, not exception)
- `unsupported_format`
- `scala_out_of_scope`
- `unsupported_scala` (returns empty IR)
- `dynamic_table_name` (partial lineage tagged)
- `lineage_partial` / `lineage_conditional` flags on affected DataFrames

---

## 19. Warning taxonomy (full list)

| Type | Subtypes |
|---|---|
| `syntax_error` / `sql_parse_error` / `parse_error` | – |
| `unsupported_format` / `scala_out_of_scope` | – |
| `dynamic_table_name` / `lineage_partial` / `lineage_conditional` | – |
| `runtime_dynamic` | `eval`, `setattr`, `dynamic_binding`, `reflection`, `sql_template`, `dynamic_loop` |
| `hidden_state` | `out_of_order_execution` |
| `column_shadowing` | `select_alias_duplicate`, `withColumn_overwrite` |
| `external_callback` | – |
| `import_depth_exceeded` / `import_target_missing` | – |
| `delta_log_missing` / `delta_log_empty` / `delta_log_parse_error` / `delta_log_schema_error` | – |
| `event_log_missing` / `event_log_parse_error` | – |
| `runtime_correlation_missing` / `runtime_dag_divergence` | – |
| `unity_catalog_mismatch` | – |
| `airflow_parse_error` / `workflow_parse_error` / `spark_submit_read_error` | – |
| `neo4j_write_failed` | – |

---

## 20. Test coverage

- **206 internal tests** (unit + integration) — all passing
- **128 frontend lineage-contract tests** — all passing
- **9 Neo4j-gated tests** — activate automatically with `testcontainers[neo4j]` + Docker
- **1 documented xfail** (two-hop nested cross-module inlining — known limitation)
- Stress fixtures: 60 tables / 120 joins / 20 CTE layers parse deterministically
- Cross-file fixtures: `util_lib_pipeline`, `relative_imports`, `package_dag`, `cyclic_imports`

### Test growth across phases

| Phase | Suite | Tests | Delta |
|---|---|---:|---:|
| v0.1 baseline | internal | 101 | – |
| Phase 1 (cross-file + runtime-dynamic) | internal | 135 | +34 |
| Phase 2 (notebooks + SQL grammar) | internal | 148 | +13 |
| Phase 3 (schema + enterprise runtime) | internal | 164 | +16 |
| Phase 4 (orchestration + connectors + abstractions) | internal | 185 | +21 |
| Phase 5 (federation + runtime collector) | internal | **206** | +21 |
| Frontend contracts (held flat) | frontend | 128 | 0 across all phases |

---

## 21. Known limitations (deferred to v0.3)

- **Two-hop nested cross-module inlining** — when an inlined function calls another imported function, the inner inline sees only the caller-module's external-functions table.
- **Closure-capture in HOF factories** — outer-arg values do not propagate into the inner closure body (Phase 4 fixture uses literal-arg closures).
- **ABC concrete-subclass enumeration** — single-class method dispatch works; abstract → concrete fan-out is deferred.
- **Live Spark UI polling** — beyond the offline event-log primary path. The REST client exists; an end-to-end live-cluster integration test is deferred.

---

## 22. File layout (new in v0.2)

```
src/spark_parser/
├── connectors/                # §9 — Kafka, Iceberg, Hudi, Snowflake, BigQuery, Redshift
│   └── __init__.py
├── federation/                # §10 — OpenLineage / Unity Catalog / cross-parser helpers
│   ├── cross_parser.py
│   ├── openlineage_emitter.py
│   └── unity_catalog.py
├── orchestration/             # §7 — Airflow / Databricks / spark-submit
│   ├── airflow_parser.py
│   ├── databricks_workflow.py
│   └── spark_submit.py
├── project/                   # §1 — multi-file resolution
│   ├── module_resolver.py
│   └── project_parser.py
├── pyspark/
│   ├── runtime_dynamic.py     # §3
│   └── visitor.py             # extended for §5 / §6 / §8
└── runtime/                   # §5 + §11 — Delta log + Spark event log
    ├── delta_log.py
    ├── event_log_reader.py
    ├── plan_correlator.py
    └── spark_ui_client.py
```

---

## 23. Reproducing the test run

```bash
# Internal suite (unit + integration)
cd spark-parser
pip install -e .
python3 -m pytest                               # 206 passed, 9 skipped, 1 xfailed

# Frontend lineage-contract suite (parser-agnostic golden contracts)
cd ../frontend-test/spark-parser-tests
pip install -r requirements.txt
python3 -m pytest                               # 128 passed

# Optional — activate the 9 Neo4j integration tests via testcontainers
pip install 'testcontainers[neo4j]'
docker info > /dev/null   # confirm Docker is reachable
cd ../../spark-parser && python3 -m pytest      # 215 passed, 0 skipped (no Neo4j skip)

# Or via explicit Neo4j connection
NEO4J_URI=bolt://localhost:7687 \
NEO4J_USER=neo4j \
NEO4J_PASSWORD=password \
    python3 -m pytest integration/ -m neo4j -v
```

---

## 24. Provenance

- v0.1 implementation plan: `spark-parser-plan.md`
- v0.1 test report: `spark-parser-test-report.md`
- v0.2 implementation plan: `~/.claude/plans/here-are-the-features-fluffy-beaver.md`
- v0.2 phases: 1 (cross-file + runtime-dynamic), 2 (notebooks + SQL grammar), 3 (schema + enterprise runtime), 4 (orchestration + connectors + abstractions), 5 (federation + runtime collector)
