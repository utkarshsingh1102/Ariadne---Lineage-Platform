"""Regression fixture — real-world entry-point shape.

Two things to exercise:
  1. ``if __name__ == "__main__":`` wraps the run call in
     ``try / except / finally``. The visitor must descend into the try
     body (otherwise the main pipeline call is silently dropped).
  2. Module-level ``SCHEMA = StructType([...])`` must NOT be classified
     as an anonymous DataFrame — it's a type definition.
"""
from pyspark.sql import SparkSession
from pyspark.sql.types import StructType, StructField, StringType, IntegerType


ORDER_SCHEMA = StructType([
    StructField("order_id", StringType(), False),
    StructField("amount", IntegerType(), True),
])


def read_orders(spark):
    return spark.read.schema(ORDER_SCHEMA).parquet("s3://bucket/orders/")


def run_pipeline(spark):
    df_orders = read_orders(spark)
    df_clean = df_orders.filter("amount > 0")
    df_clean.write.mode("overwrite").parquet("s3://bucket/gold/orders/")
    return {"rows": 0}


if __name__ == "__main__":
    spark = SparkSession.builder.appName("entry").getOrCreate()
    try:
        metrics = run_pipeline(spark)
    finally:
        spark.stop()
