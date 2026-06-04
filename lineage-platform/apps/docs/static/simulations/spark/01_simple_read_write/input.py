# Fixture 01 — Easiest case
# Read one parquet path, write to one Hive table. No transformations.
# Plan §2.1.

from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("simple_etl").getOrCreate()

orders = spark.read.format("parquet").load("s3://raw/orders/")

orders.write.format("delta").mode("overwrite").saveAsTable("prod.mart.orders")
