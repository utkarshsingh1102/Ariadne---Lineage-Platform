# Spark Test Fixtures

| File | Format | Plan section | Covers |
|---|---|---|---|
| `pyspark/01_simple_read_write.py` | PySpark | §2.1, §6 | Single parquet read + Delta saveAsTable — smoke test |
| `pyspark/02_join_and_select.py` | PySpark | §2.1, §6 step 6 | Left join + projected `.select()` |
| `pyspark/03_with_column_chain.py` | PySpark | §2.1, §6 step 7 | `withColumn` / `cast` / `when` / `lit` / `withColumnRenamed` / `drop` |
| `pyspark/04_groupby_agg.py` | PySpark | §6 step 6 | `groupBy().agg()` with multiple aggregations |
| `pyspark/05_union.py` | PySpark | §6 step 6 | `unionByName` + `dropDuplicates` |
| `pyspark/06_udf_usage.py` | PySpark | §2.4, §6 | `@udf` + `@pandas_udf` — body not introspected |
| `pyspark/07_dynamic_table_name.py` | PySpark | §14 | Static-resolvable env var + unresolvable `sys.argv[1]` — partial-lineage flag |
| `pyspark/08_spark_sql_inside.py` | PySpark | §2.1, §6 step 8 | `spark.sql("...")` extraction + temp view + CTAS via SQL |
| `pyspark/09_realistic_etl.py` | PySpark | all of §2 + §6 + §14 | Kitchen sink: JDBC + S3 + ADLS, UDF, MERGE INTO, 3 write targets |
| `sparksql/01_simple_ctas.sql` | Spark SQL | §2.2 | `CREATE TABLE AS SELECT` |
| `sparksql/02_insert_overwrite.sql` | Spark SQL | §9.2 | `INSERT OVERWRITE` with explicit column list |
| `sparksql/03_merge_into.sql` | Spark SQL | §9.2 | `MERGE INTO` with WHEN MATCHED / WHEN NOT MATCHED |
| `sparksql/04_cte_chain.sql` | Spark SQL | §9.2 | CTE chain + `ROW_NUMBER() OVER` + `UNION ALL` |
| `sparksql/05_partition_write.sql` | Spark SQL | §9.2 | `INSERT OVERWRITE ... PARTITION` |
| `notebooks/01_simple.ipynb` | Jupyter | §2.3 | Vanilla Jupyter `.ipynb` with two Python cells |
| `notebooks/02_databricks_format.py` | Databricks | §2.3 | `# Databricks notebook source` header + `# COMMAND ----------` separators + `dbutils.widgets` |
| `notebooks/03_mixed_python_sql.ipynb` | Jupyter | §2.3, §6 step 3 | Mixed Python + SQL cells — parser routes each to the right backend |

## Provenance

All fixtures are hand-written, minimised. None contain real customer data. When real samples are sourced (plan §10) — e.g. StockPulse Glue PySpark, Databricks Solution Accelerators — add as `10_*`, `11_*`, etc. and update this table.
