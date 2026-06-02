"""ADLS abfss:// (container@account.dfs.core.windows.net) path."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

df = spark.read.parquet(
    "abfss://orders@prodlake.dfs.core.windows.net/bronze/orders/",
)
