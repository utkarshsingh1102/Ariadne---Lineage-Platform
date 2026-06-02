"""Higher-order factory pattern — a function that returns a closure.

The inner closure uses literal column names rather than closure-captured
variables; closure-binding inference is out of Phase 4 scope (it would need
to track which outer-arg constants survive into the inner FunctionDef).
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, upper


def make_region_upper():
    def _xf(df):
        return df.withColumn("region_upper", upper(col("region")))
    return _xf


spark = SparkSession.builder.getOrCreate()

orders = spark.read.format("parquet").load("s3://raw/orders/")
xf = make_region_upper()
enriched = xf(orders)
enriched.write.format("delta").saveAsTable("prod.mart.orders_hof")
