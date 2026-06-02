"""Callback-driven transforms — ``.transform(local_fn)`` + external callable."""
from pyspark.sql import SparkSession
from pyspark.sql.functions import col


def add_revenue(df):
    return df.withColumn("revenue", col("amount") * col("quantity"))


spark = SparkSession.builder.getOrCreate()

orders = spark.read.format("parquet").load("s3://raw/orders/")
local_out = orders.transform(add_revenue)             # local — should inline
local_out.write.format("delta").saveAsTable("prod.mart.orders_local")

external_out = orders.transform(some_external_fn)     # external — should warn
external_out.write.format("delta").saveAsTable("prod.mart.orders_external")
