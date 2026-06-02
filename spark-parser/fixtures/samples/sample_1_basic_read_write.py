"""
Sample 1 — basic read / write.

The simplest possible PySpark lineage: read a Hive table, write to another.
Produces 1 :SparkScript, 2 :DataFrame, 2 :Table, READS_TABLE + WRITES_TABLE
edges.
"""
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("sample_1_basic").getOrCreate()

orders = spark.table("bronze.orders_raw")

orders.write.mode("overwrite").saveAsTable("silver.orders")
