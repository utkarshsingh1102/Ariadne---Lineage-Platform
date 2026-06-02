"""A column renamed twice — a → b → c. The rename history should be retained."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

raw = spark.table("prod.raw.orders")
out = (
    raw
    .withColumnRenamed("amount", "value")
    .withColumnRenamed("value", "order_value")
)
out.write.format("delta").saveAsTable("prod.mart.orders_renamed")
