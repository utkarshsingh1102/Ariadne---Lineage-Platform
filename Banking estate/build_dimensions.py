"""
build_dimensions.py — Builds conformed dimensions from the raw layer.
Triggered by TWS job BUILD_DIMENSIONS (FOLLOWS INGEST_RAW).
Reads:  prod.raw.customers, prod.raw.accounts
Writes: prod.dim.customers, prod.dim.accounts, prod.dim.branch
"""
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.builder.appName("build_dimensions").getOrCreate()

raw_customers = spark.table("prod.raw.customers")
raw_accounts = spark.table("prod.raw.accounts")

# --- DIM CUSTOMERS: clean + derive region from country code ---
dim_customers = (
    raw_customers
    .withColumn("region", F.when(F.col("country").isin("US", "CA"), F.lit("Americas"))
                            .when(F.col("country").isin("GB", "DE", "FR"), F.lit("EMEA"))
                            .otherwise(F.lit("APAC")))
    .select(
        F.col("customer_id"),
        F.col("name").alias("customer_name"),
        F.col("segment"),
        F.col("region"),
        F.col("country"),
    )
)
dim_customers.write.format("delta").mode("overwrite").saveAsTable("prod.dim.customers")

# --- DIM ACCOUNTS ---
dim_accounts = raw_accounts.select(
    F.col("account_id"),
    F.col("customer_id"),
    F.col("account_type"),
    F.col("branch_id"),
    F.col("open_date"),
    F.col("status"),
)
dim_accounts.write.format("delta").mode("overwrite").saveAsTable("prod.dim.accounts")

# --- DIM BRANCH: derived directly from accounts via SQL ---
raw_accounts.createOrReplaceTempView("raw_accounts")
dim_branch = spark.sql("""
    SELECT DISTINCT branch_id,
           branch_name,
           branch_region AS region
    FROM raw_accounts
    WHERE branch_id IS NOT NULL
""")
dim_branch.write.format("delta").mode("overwrite").saveAsTable("prod.dim.branch")

spark.stop()
