"""Helper inside a package — imported via `from .transforms import clean`."""
from pyspark.sql.functions import col


def clean(df):
    return df.filter(col("status") == "ok").withColumn("upper_name", col("name").cast("string"))
