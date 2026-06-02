"""Regression fixture — dataframe_collapse_plan.md.

Exercises every anchor classification rule:

  - ``df_raw`` is an IO source (anchor).
  - ``claims`` is a named variable assignment whose chain is dropDuplicates,
    withColumn, filter (3 intermediates → 1 anchor).
  - ``enriched`` is a FORK: consumed by both ``summary`` and a direct write,
    so it must stay its own anchor.
  - ``summary`` is a temp view registration.
  - The final ``.saveAsTable(...)`` produces an IO-sink anchor.
"""
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.builder.appName("anchor_chain").getOrCreate()

df_raw = spark.read.parquet("s3a://bronze/claims/")

claims = (
    df_raw
    .dropDuplicates(["claim_id"])
    .withColumn("denied", F.col("paid_amount") == 0)
    .filter(F.col("claim_id").isNotNull())
)

enriched = claims.withColumn("annualised", F.col("paid_amount") * 12)
summary = enriched.groupBy("payer_id").agg(F.sum("paid_amount").alias("total_paid"))

summary.createOrReplaceTempView("v_claims_summary")
enriched.write.mode("overwrite").parquet("s3a://gold/claims_enriched/")
