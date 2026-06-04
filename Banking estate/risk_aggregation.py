"""
risk_aggregation.py — Aggregates exposure and daily balances for risk reporting.
Triggered by TWS job RISK_AGG (FOLLOWS FRAUD_SCORING).
Reads:  prod.fact.transactions, prod.fact.balances, prod.dim.branch, prod.mart.fraud_scores
Writes: prod.mart.risk_exposure, prod.mart.daily_balances
"""
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.builder.appName("risk_aggregation").getOrCreate()

fact_txn = spark.table("prod.fact.transactions")
fact_bal = spark.table("prod.fact.balances")
dim_branch = spark.table("prod.dim.branch")
fraud = spark.table("prod.mart.fraud_scores")

# --- Daily balances per branch/region ---
fact_txn.createOrReplaceTempView("fact_transactions")
dim_branch.createOrReplaceTempView("dim_branch")

daily = spark.sql("""
    SELECT  t.txn_date,
            t.branch_id,
            b.region,
            SUM(t.amount)         AS daily_net_amount,
            COUNT(t.txn_id)       AS daily_txn_count,
            COUNT(DISTINCT t.customer_id) AS active_customers
    FROM fact_transactions t
    INNER JOIN dim_branch b ON t.branch_id = b.branch_id
    GROUP BY t.txn_date, t.branch_id, b.region
""")
daily.write.format("delta").mode("overwrite").saveAsTable("prod.mart.daily_balances")

# --- Risk exposure: combine balances with fraud signal ---
fraud_by_cust = (
    fraud.groupBy("customer_id", "region")
    .agg(F.max("fraud_score").alias("max_fraud_score"),
         F.sum(F.when(F.col("fraud_flag"), 1).otherwise(0)).alias("flagged_txns"))
)

exposure = (
    fact_bal.join(fraud_by_cust, on="customer_id", how="left")
    .withColumn("exposure_amount", F.abs(F.col("net_balance_change")))
    .withColumn("risk_weighted_exposure",
                F.col("exposure_amount") * (F.lit(1.0) + F.coalesce(F.col("max_fraud_score"), F.lit(0.0))))
    .select("customer_id", "branch_id", "region", "exposure_amount",
            "max_fraud_score", "flagged_txns", "risk_weighted_exposure")
)
exposure.write.format("delta").mode("overwrite").saveAsTable("prod.mart.risk_exposure")

spark.stop()
