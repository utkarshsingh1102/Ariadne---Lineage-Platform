"""M3b fixture — star projection + chained .alias().

Three things to exercise:
  - select("*") must emit a star-marker derivation, not silently drop the arg.
  - select("*", col("foo")) inherits every column of the source plus foo.
  - col("x").alias("inner").alias("outer") must record both alias hops.
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import col

spark = SparkSession.builder.appName("star_alias").getOrCreate()

orders = spark.read.table("bronze.orders")
extras = orders.select("order_id", "amount")

# Star + additional column.
projected = orders.select("*", col("country"))

# Chained alias: inner = "raw_amt" (intermediate), outer = "amt" (target).
aliased = orders.select(col("amount").alias("raw_amt").alias("amt"))
