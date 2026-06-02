"""Loop over a runtime-resolved list — yields a dynamic_loop warning.

``tables`` is whatever ``os.environ`` happens to contain at runtime, so the
static parser must mark the loop body as partial lineage.
"""
import os

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

tables = os.environ["TABLES"].split(",")   # not statically known

for t in tables:
    df = spark.table(t)
    df.write.format("delta").mode("append").saveAsTable("prod.mart.aggregate_all")
