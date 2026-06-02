"""Class-based ETL framework — a single class with two transformation methods.

Verifies v0.2 §8 inlining: ``proc.enrich(orders)`` should pick up the column
``revenue`` added inside the method body, even though the method lives on a
class rather than as a free function.
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import col


class OrderProcessor:
    def enrich(self, orders):
        return orders.withColumn("revenue", col("amount") * col("quantity"))

    def aggregate(self, orders):
        return orders.groupBy("region").agg({"revenue": "sum"})


spark = SparkSession.builder.getOrCreate()

orders = spark.read.format("parquet").load("s3://raw/orders/")
proc = OrderProcessor()
enriched = proc.enrich(orders)
enriched.write.format("delta").saveAsTable("prod.mart.orders_classed")
