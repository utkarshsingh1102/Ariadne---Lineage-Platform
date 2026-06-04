"""
ingest_raw.py — Lands raw banking source files into the raw layer.
Triggered by TWS job INGEST_RAW in schedule DAILY_CORE_BANKING_LOAD.
Writes: prod.raw.accounts, prod.raw.transactions, prod.raw.customers
"""
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.builder.appName("ingest_raw").getOrCreate()

# --- Source: core banking extract (parquet on S3) ---
accounts = spark.read.format("parquet").load("s3://raw/core/accounts/")
accounts = accounts.withColumn("ingested_at", F.current_timestamp())
accounts.write.format("delta").mode("overwrite").saveAsTable("prod.raw.accounts")

# --- Source: transaction extract (CSV on S3) ---
transactions = (
    spark.read.format("csv")
    .option("header", "true")
    .load("s3://raw/core/transactions/")
)
transactions = transactions.withColumn("amount", F.col("amount").cast("double"))
transactions.write.format("delta").mode("overwrite").saveAsTable("prod.raw.transactions")

# --- Source: CRM customers over JDBC ---
customers = (
    spark.read.format("jdbc")
    .option("url", "jdbc:postgresql://crm-prod.corp.local:5432/crm")
    .option("dbtable", "public.customers")
    .load()
)
customers.write.format("delta").mode("overwrite").saveAsTable("prod.raw.customers")

spark.stop()
