# Databricks notebook source
# fraud_scoring.py — Scores transactions for fraud risk.
# Triggered by TWS job FRAUD_SCORING (FOLLOWS TRANSFORM_TXNS).
# Reads:  prod.fact.transactions, prod.dim.customers
# Writes: prod.mart.fraud_scores

# COMMAND ----------

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

spark = SparkSession.builder.appName("fraud_scoring").getOrCreate()

# COMMAND ----------

fact_txn = spark.table("prod.fact.transactions")
dim_cust = spark.table("prod.dim.customers")

# COMMAND ----------

# Velocity feature: count of txns per customer in a rolling window
w = Window.partitionBy("customer_id").orderBy("txn_date").rowsBetween(-9, 0)
features = fact_txn.withColumn("txn_velocity_10", F.count("txn_id").over(w))
features = features.withColumn("amount_zscore",
                               (F.col("amount_abs") - F.avg("amount_abs").over(Window.partitionBy("customer_id")))
                               / F.stddev("amount_abs").over(Window.partitionBy("customer_id")))

# COMMAND ----------

# Rule-based fraud score (column-level derivation from features)
scored = features.withColumn(
    "fraud_score",
    F.when(F.col("amount_abs") > 10000, F.lit(0.8))
     .when(F.col("txn_velocity_10") > 8, F.lit(0.6))
     .when(F.col("amount_zscore") > 3, F.lit(0.5))
     .otherwise(F.lit(0.1))
)
scored = scored.withColumn("fraud_flag", F.col("fraud_score") >= F.lit(0.5))

# COMMAND ----------

# Join customer region for downstream BI
scored.createOrReplaceTempView("scored_txns")
dim_cust.createOrReplaceTempView("dim_customers")

fraud_out = spark.sql("""
    SELECT  s.txn_id,
            s.customer_id,
            s.account_id,
            s.branch_id,
            c.region,
            s.amount_abs,
            s.fraud_score,
            s.fraud_flag,
            s.txn_date
    FROM scored_txns s
    INNER JOIN dim_customers c ON s.customer_id = c.customer_id
""")

fraud_out.write.format("delta").mode("overwrite").saveAsTable("prod.mart.fraud_scores")

spark.stop()
