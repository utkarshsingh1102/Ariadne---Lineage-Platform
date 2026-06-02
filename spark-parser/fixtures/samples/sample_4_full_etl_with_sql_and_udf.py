"""
Sample 4 — realistic ETL.

Multi-source pipeline that exercises every interesting parser path:

* three source tables joined via an inline ``spark.sql`` block (CTE + join),
* a UDF that derives a new column from two raw columns,
* a chain of ``withColumn`` derivations,
* two write targets — a star-schema fact and a dimension — both written from
  the same script.

This is the most useful sample for end-to-end lineage demos: a single
:SparkScript node fans out to 3 source tables, 2 target tables, a :UDF
node, and many :Attribute / DERIVES_FROM edges.
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import udf, col, when, broadcast
from pyspark.sql.types import StringType

spark = SparkSession.builder.appName("sample_4_full_etl").getOrCreate()


@udf(returnType=StringType())
def classify_customer(total_revenue, order_count):
    """Bucket customers by lifetime value and frequency."""
    if total_revenue is None or order_count is None:
        return "unknown"
    if total_revenue >= 10000 and order_count >= 20:
        return "platinum"
    if total_revenue >= 5000:
        return "gold"
    if total_revenue >= 1000:
        return "silver"
    return "bronze"


# ---------------------------------------------------------------------------
# 1. Inline Spark SQL — joins 3 sources via a CTE chain
# ---------------------------------------------------------------------------
joined_sql = """
WITH active_orders AS (
    SELECT
        o.order_id,
        o.customer_id,
        o.total_amount,
        o.order_date
    FROM   silver.orders o
    WHERE  o.status = 'completed'
),
enriched AS (
    SELECT
        ao.order_id,
        ao.customer_id,
        ao.total_amount,
        ao.order_date,
        c.country,
        c.signup_date,
        p.category,
        p.product_name
    FROM   active_orders        ao
    JOIN   silver.customers     c  ON c.id = ao.customer_id
    JOIN   silver.order_items   oi ON oi.order_id = ao.order_id
    JOIN   silver.products      p  ON p.id = oi.product_id
)
SELECT * FROM enriched
"""

enriched_df = spark.sql(joined_sql)

# ---------------------------------------------------------------------------
# 2. Derived columns + UDF
# ---------------------------------------------------------------------------
with_lifetime = (
    enriched_df
    .groupBy("customer_id", "country")
    .agg(
        {"total_amount": "sum", "order_id": "count"},
    )
    .withColumnRenamed("sum(total_amount)", "total_revenue")
    .withColumnRenamed("count(order_id)", "order_count")
)

with_segment = with_lifetime.withColumn(
    "segment",
    classify_customer(col("total_revenue"), col("order_count")),
)

with_flag = with_segment.withColumn(
    "is_high_value",
    when(col("total_revenue") > 5000, True).otherwise(False),
)

# ---------------------------------------------------------------------------
# 3. Two write targets
# ---------------------------------------------------------------------------

# Dimension — customer segmentation
with_flag.write.mode("overwrite").saveAsTable("gold.dim_customer_segment")

# Fact — order detail enriched with segment
fact_orders = enriched_df.join(
    broadcast(with_segment.select("customer_id", "segment")),
    on="customer_id",
    how="left",
)
fact_orders.write.mode("overwrite").partitionBy("country").saveAsTable(
    "gold.fact_orders_enriched"
)
