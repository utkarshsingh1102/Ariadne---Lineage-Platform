"""Snowflake via the canonical ``sfUrl`` option."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

df = (
    spark.read.format("snowflake")
    .option("sfUrl", "ab12345.snowflakecomputing.com")
    .option("sfDatabase", "PROD")
    .option("sfSchema", "DIM")
    .option("sfWarehouse", "ETL_WH")
    .option("dbtable", "CUSTOMERS")
    .option("sfUser", "etl_role")
    .option("sfPassword", "no-leak")
    .load()
)
