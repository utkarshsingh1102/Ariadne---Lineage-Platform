"""M2a fixture — tuple return + tuple LHS.

`split` returns two DataFrames; caller binds them with a tuple LHS.
The analyser should bind the first LHS name to the first returned DF
and emit a `tuple_return_partial` warning explaining the second is
dropped (full multi-bind is parked as a known limitation).
"""
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("tuple_return").getOrCreate()


def split(df):
    paid = df.filter("status = 'PAID'")
    unpaid = df.filter("status = 'UNPAID'")
    return paid, unpaid


orders = spark.read.table("bronze.orders")
paid_df, unpaid_df = split(orders)
