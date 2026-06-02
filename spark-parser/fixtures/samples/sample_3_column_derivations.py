"""
Sample 3 — derived columns + aggregation.

A single source fans into a target with a chain of derived attributes.
Showcases the parser's column-lineage path: each ``withColumn`` produces an
:Attribute with a DERIVES_FROM edge to its inputs, and the final ``groupBy``
+ ``agg`` records measure-level lineage.
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, when, sum as _sum, count

spark = SparkSession.builder.appName("sample_3_derivations").getOrCreate()

orders = spark.table("silver.orders_with_customers")

# Step 1: derived flag
flagged = orders.withColumn(
    "is_premium",
    when(col("total_amount") > 1000, True).otherwise(False),
)

# Step 2: derived monetary column (depends on existing column + tax rate)
with_tax = flagged.withColumn(
    "amount_with_tax",
    col("total_amount") * 1.18,
)

# Step 3: another derivation that depends on the previous one
with_margin = with_tax.withColumn(
    "margin",
    col("amount_with_tax") - col("cost"),
)

# Step 4: rollup
customer_metrics = with_margin.groupBy("customer_id", "region").agg(
    _sum("amount_with_tax").alias("total_revenue"),
    _sum("margin").alias("total_margin"),
    count("order_id").alias("order_count"),
)

customer_metrics.write.mode("overwrite").saveAsTable("gold.customer_metrics")
