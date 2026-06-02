"""Leaf utility — no further imports."""
from pyspark.sql.functions import col


def add_metadata(df):
    return df.withColumn("ingested_at", col("event_time"))
