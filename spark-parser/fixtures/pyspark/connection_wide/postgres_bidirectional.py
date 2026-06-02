"""Postgres bidirectional read + write — one Connection node, two edges."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

URL = "jdbc:postgresql://rds-orders.us-east-1.rds.amazonaws.com:5432/ecommerce"

df = (
    spark.read.format("jdbc")
    .option("url", URL)
    .option("dbtable", "public.orders")
    .load()
)

(
    df.write.format("jdbc")
    .option("url", URL)
    .option("dbtable", "reporting.daily_order_summary")
    .mode("overwrite")
    .save()
)
