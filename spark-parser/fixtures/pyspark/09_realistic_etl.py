# Fixture 09 — Kitchen-sink realistic ETL
# Exercises every construct simultaneously:
#   - Multiple read formats (parquet, delta, JDBC)
#   - Static + qualified table references
#   - Reassigned variables (creation_order incrementing)
#   - withColumn chain, when/otherwise, cast, lit
#   - Join (left), groupBy + agg, union
#   - UDF and pandas_udf
#   - Method-chain DataFrame (no intermediate variable → anonymous IR)
#   - spark.sql(...) for a MERGE INTO
#   - Multiple write targets (saveAsTable + insertInto + save)
#   - cache(), repartition() — should NOT affect lineage

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col, lit, when, sum as _sum, count, broadcast, udf
)
from pyspark.sql.types import StringType

spark = SparkSession.builder.appName("realistic_etl").getOrCreate()


@udf(returnType=StringType())
def tier_of(amount):
    if amount is None:
        return "UNKNOWN"
    if amount > 10000:
        return "PLATINUM"
    if amount > 1000:
        return "GOLD"
    return "SILVER"


# -----------------------------------------------------------------------------
# Sources
# -----------------------------------------------------------------------------
orders_raw = spark.read.format("parquet").load("s3://raw/orders/")
customers = spark.table("prod.dim.customers")
products = spark.read.format("delta").load("abfss://lake@acct/products/")

fx = (
    spark.read
    .format("jdbc")
    .option("url", "jdbc:postgresql://fx-host:5432/fx")
    .option("dbtable", "rates_daily")
    .load()
)

# -----------------------------------------------------------------------------
# Variable reassignment (plan §14 — creation_order increments)
# -----------------------------------------------------------------------------
orders = orders_raw.filter(col("status") == "CONFIRMED")
orders = orders.withColumn("amount_with_tax", col("amount") * lit(1.18))
orders = orders.withColumn("tier", tier_of(col("amount")))

# -----------------------------------------------------------------------------
# Joins (broadcast hint — must not affect lineage)
# -----------------------------------------------------------------------------
enriched = (
    orders
    .join(broadcast(customers), orders.customer_id == customers.id, "left")
    .join(products, orders.product_id == products.product_id, "inner")
    .join(fx, orders.currency == fx.ccy, "left")
)

# -----------------------------------------------------------------------------
# Aggregation
# -----------------------------------------------------------------------------
summary = (
    enriched
    .groupBy("region", "tier")
    .agg(
        _sum("amount_with_tax").alias("revenue_tax_incl"),
        count("order_id").alias("order_count"),
    )
)

# -----------------------------------------------------------------------------
# Union with archive (anonymous intermediate DataFrame)
# -----------------------------------------------------------------------------
all_summary = summary.unionByName(
    spark.read.format("parquet").load("s3://archive/summary/"),
    allowMissingColumns=True,
).cache().repartition(8)

# -----------------------------------------------------------------------------
# MERGE INTO via spark.sql
# -----------------------------------------------------------------------------
all_summary.createOrReplaceTempView("staged_summary")
spark.sql("""
    MERGE INTO prod.mart.customer_summary t
    USING staged_summary s
    ON t.region = s.region AND t.tier = s.tier
    WHEN MATCHED THEN UPDATE SET
        t.revenue_tax_incl = s.revenue_tax_incl,
        t.order_count = s.order_count
    WHEN NOT MATCHED THEN INSERT *
""")

# -----------------------------------------------------------------------------
# Multiple write targets
# -----------------------------------------------------------------------------
enriched.write.format("delta").mode("overwrite").saveAsTable("prod.mart.orders_enriched")
summary.write.format("delta").mode("append").insertInto("prod.mart.summary_daily")
all_summary.write.format("parquet").mode("overwrite").save("s3://mart/summary/")
