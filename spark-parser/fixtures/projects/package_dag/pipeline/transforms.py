"""Intermediate module — imports utils and is imported by main."""
from pyspark.sql.functions import col

from .utils import add_metadata


def enrich(df):
    return add_metadata(df).withColumn("upper_country", col("country").cast("string"))
