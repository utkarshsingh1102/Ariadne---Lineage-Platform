"""Reflection — column accessed via ``getattr`` with a runtime name."""
import os

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

df = spark.table("prod.dim.customers")
field = os.environ.get("CUSTOM_FIELD", "name")
selected = getattr(df, field)         # reflection — non-constant attribute name
df.select(selected).write.format("delta").saveAsTable("prod.mart.reflection_out")
