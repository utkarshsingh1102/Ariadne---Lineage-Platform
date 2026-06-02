"""
Sample 2 — inner join + filter + explicit select.

Two source tables fan into one target through a join, a filter, and an
explicit ``select`` that pins the output schema. Without the ``select``,
the parser can't know the column list (PySpark only resolves it at runtime
against the catalog) and the target table shows up schemaless. The
``select`` makes the columns of ``silver.orders_with_customers`` explicit
so they appear under the Table node.
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import col

spark = SparkSession.builder.appName("sample_2_join").getOrCreate()

orders = spark.table("bronze.orders_raw")
customers = spark.table("bronze.customers_raw")

joined = orders.join(customers, orders.customer_id == customers.id, "inner")

active_orders = (
    joined
    .filter(col("status") == "active")
    .select(
        col("order_id"),
        col("customer_id"),
        col("total_amount"),
        col("status"),
        col("country"),
        col("signup_date"),
    )
)

active_orders.write.mode("overwrite").saveAsTable("silver.orders_with_customers")
