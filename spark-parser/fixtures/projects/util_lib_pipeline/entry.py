"""Entry point that imports a helper and writes a final table.

The lineage chain spans two files:
    spark.read.parquet("s3://raw/orders/")  →  util.enrich(...)  →
    saveAsTable("prod.mart.orders_enriched")
"""
from pyspark.sql import SparkSession
from util import enrich

spark = SparkSession.builder.appName("orders_etl").getOrCreate()

orders = spark.read.format("parquet").load("s3://raw/orders/")
enriched = enrich(orders)
enriched.write.format("delta").mode("overwrite").saveAsTable("prod.mart.orders_enriched")
