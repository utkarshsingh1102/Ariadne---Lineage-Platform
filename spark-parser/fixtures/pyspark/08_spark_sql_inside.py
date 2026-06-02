# Fixture 08 — spark.sql("...") inside PySpark
# The SQL string must be extracted and handed to sqlglot.
# Plan §2.1 + §6 step 8.

from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("sql_inside").getOrCreate()

# Temp view first
raw = spark.read.format("parquet").load("s3://raw/orders/")
raw.createOrReplaceTempView("raw_orders")

# Plain SQL with explicit FROM list
enriched = spark.sql("""
    SELECT
        o.order_id,
        o.customer_id,
        o.amount,
        c.region
    FROM raw_orders o
    INNER JOIN prod.dim.customers c
        ON o.customer_id = c.id
    WHERE o.amount > 0
""")

# CTAS issued via spark.sql
spark.sql("""
    CREATE OR REPLACE TABLE prod.mart.orders_sql AS
    SELECT * FROM raw_orders WHERE amount IS NOT NULL
""")

enriched.write.format("delta").mode("overwrite").saveAsTable("prod.mart.orders_enriched_sql")
