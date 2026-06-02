# Fixture 02 — Join + select
# Two sources, a left-join on customer_id, projected columns.
# Plan §2.1 (the canonical example) + §6 step 6 (join handling).

from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("join_etl").getOrCreate()

orders = spark.read.format("parquet").load("s3://raw/orders/")
customers = spark.table("prod.dim.customers")

enriched = (
    orders
    .join(customers, orders.customer_id == customers.id, "left")
    .select("order_id", "customer_id", "amount", "region")
)

enriched.write.format("delta").mode("overwrite").saveAsTable("prod.mart.orders_enriched")
