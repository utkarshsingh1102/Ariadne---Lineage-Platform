"""M3a fixture — selectExpr SQL strings.

Each projection should yield a derivation whose target is the aliased name and
whose sources are every column referenced in the expression.
"""
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("selectexpr").getOrCreate()

orders = spark.read.table("bronze.orders")

priced = orders.selectExpr(
    "order_id",
    "amount * 1.18 AS taxed_amount",
    "CASE WHEN status = 'PAID' THEN 1 ELSE 0 END AS is_paid",
    "concat(country, '-', region) AS market",
)
