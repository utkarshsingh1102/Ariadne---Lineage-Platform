"""Templated SQL with an unresolvable interpolation.

``env`` is read from ``sys.argv`` at runtime — the parser cannot bind it
statically, so the templated query should yield a ``sql_template`` warning.
"""
import sys

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

env = sys.argv[1]                   # noqa: B007 — runtime value
query = f"SELECT * FROM {env}.orders"
df = spark.sql(query)
df.write.format("delta").saveAsTable("prod.mart.orders_template_out")
