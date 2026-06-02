# Fixture 04 — groupBy + agg
# Asserts DERIVES_FROM edges with via='agg'.
# Plan §6 step 6.

from pyspark.sql import SparkSession
from pyspark.sql.functions import sum as _sum, count, avg

spark = SparkSession.builder.appName("agg_etl").getOrCreate()

orders = spark.table("prod.raw.orders")

summary = (
    orders
    .groupBy("customer_id", "region")
    .agg(
        _sum("amount").alias("total_amount"),
        count("order_id").alias("order_count"),
        avg("amount").alias("avg_amount"),
    )
)

summary.write.format("delta").mode("overwrite").saveAsTable("prod.mart.customer_summary")
