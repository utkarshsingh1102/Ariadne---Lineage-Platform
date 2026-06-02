"""Env-var URL: ``os.getenv("PG_URL")`` — node minted with resolved=False."""
import os
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

df = (
    spark.read.format("jdbc")
    .option("url", os.getenv("PG_URL"))
    .option("dbtable", "public.orders")
    .load()
)
