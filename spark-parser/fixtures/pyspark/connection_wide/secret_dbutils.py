"""Databricks secret-scope URL: ``dbutils.secrets.get(...)`` → resolved=False."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

pg_url = dbutils.secrets.get("etl", "PG_URL")  # noqa: F821 — runtime injected

df = (
    spark.read.format("jdbc")
    .option("url", pg_url)
    .option("dbtable", "public.orders")
    .load()
)
