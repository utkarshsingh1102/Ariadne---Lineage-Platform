"""Cycle: a imports b, b imports a. The parser must terminate."""
from pyspark.sql import SparkSession

from b import do_b  # noqa: F401 — imported for the cycle, not used

spark = SparkSession.builder.getOrCreate()


def do_a(df):
    return df.withColumn("a_marker", df.id)


orders = spark.read.format("parquet").load("s3://raw/orders/")
result = do_a(orders)
result.write.format("delta").mode("overwrite").saveAsTable("prod.mart.cyclic_a")
