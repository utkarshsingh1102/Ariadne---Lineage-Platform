"""
transform_transactions.py — Builds the transaction fact + balances, with a
column-level enrichment and an SCD-style MERGE into the fact table.
Triggered by TWS job TRANSFORM_TXNS (FOLLOWS BUILD_DIMENSIONS).
Reads:  prod.raw.transactions, prod.dim.customers, prod.dim.accounts
Writes: prod.fact.transactions, prod.fact.balances
"""
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType

spark = SparkSession.builder.appName("transform_transactions").getOrCreate()

raw_txn = spark.table("prod.raw.transactions")
dim_cust = spark.table("prod.dim.customers")
dim_acct = spark.table("prod.dim.accounts")


# UDF: classify a transaction channel (body not introspected by parser)
@F.udf(returnType=StringType())
def classify_channel(mcc, terminal):
    return "online" if terminal is None else "pos"


# Register views and run the enrichment join in SQL
raw_txn.createOrReplaceTempView("raw_transactions")
dim_acct.createOrReplaceTempView("dim_accounts")
dim_cust.createOrReplaceTempView("dim_customers")

enriched = spark.sql("""
    SELECT  t.txn_id,
            t.account_id,
            a.customer_id,
            c.region,
            c.segment,
            t.amount,
            t.txn_date,
            t.mcc,
            t.terminal_id,
            a.branch_id
    FROM raw_transactions t
    INNER JOIN dim_accounts a ON t.account_id = a.account_id
    INNER JOIN dim_customers c ON a.customer_id = c.customer_id
""")

# Column-level derivations
enriched = enriched.withColumn("channel", classify_channel(F.col("mcc"), F.col("terminal_id")))
enriched = enriched.withColumn("amount_abs", F.abs(F.col("amount")))
enriched = enriched.withColumn("is_debit", F.col("amount") < 0)

# Variable reassignment -> new lineage step
enriched = enriched.filter(F.col("amount").isNotNull())

# Write the transaction fact
enriched.write.format("delta").mode("overwrite").saveAsTable("prod.fact.transactions")

# --- Balances: aggregate per account, then MERGE into the balances fact ---
balances = (
    enriched.groupBy("account_id", "customer_id", "branch_id")
    .agg(F.sum("amount").alias("net_balance_change"),
         F.count("txn_id").alias("txn_count"),
         F.max("txn_date").alias("last_txn_date"))
)
balances.createOrReplaceTempView("balances_delta")

spark.sql("""
    MERGE INTO prod.fact.balances AS tgt
    USING balances_delta AS src
    ON tgt.account_id = src.account_id
    WHEN MATCHED THEN UPDATE SET
        tgt.net_balance_change = tgt.net_balance_change + src.net_balance_change,
        tgt.txn_count = tgt.txn_count + src.txn_count,
        tgt.last_txn_date = src.last_txn_date
    WHEN NOT MATCHED THEN INSERT *
""")

spark.stop()
