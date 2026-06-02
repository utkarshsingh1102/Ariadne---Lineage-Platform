"""Runtime eval — table name built by eval() of a runtime string.

The parser cannot resolve what string ``eval`` will produce; this fixture
should yield a ``runtime_dynamic / eval`` warning and ``lineage_partial=true``.
"""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

expr = "prod." + "raw" + ".orders"
table = eval(expr)  # noqa: S307 — intentional for the test
df = spark.table(table)
df.write.format("delta").saveAsTable("prod.mart.orders_eval_out")
