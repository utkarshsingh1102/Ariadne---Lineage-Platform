"""Loop over a literal list — should NOT yield a dynamic_loop warning.

This fixture verifies the resolver doesn't false-positive on the common
``for table in ["a", "b", "c"]:`` idiom — those iterations are statically
unrollable.
"""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

for tbl in ["prod.raw.orders_2023", "prod.raw.orders_2024"]:
    df = spark.table(tbl)
    df.write.format("delta").mode("append").saveAsTable(f"prod.mart.orders_all")
