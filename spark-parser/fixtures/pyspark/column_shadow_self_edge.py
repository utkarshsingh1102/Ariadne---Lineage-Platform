"""Regression fixture — ``withColumn`` overwriting an existing column.

The visitor must:
  - emit a column_shadowing warning with subtype ``info:withColumn_overwrite``
    (severity downgraded to info — the in-place rewrite is intentional Spark
    idiom);
  - emit a self-referential derivation ``risk_label`` → ``risk_label`` with
    via=``withColumn_shadow`` so the lineage shows the old column still
    feeds the new one even though the user expression
    (``F.lit("UNKNOWN")``) doesn't name it.
"""
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.builder.appName("shadow").getOrCreate()

df = spark.read.parquet("s3a://bronze/risk/")
df = df.withColumn("risk_label", F.lit("LOW"))
# Second withColumn OVERWRITES risk_label with an expression that does NOT
# reference the original column — the shadow self-edge must still preserve
# the implicit "old risk_label feeds new risk_label" lineage.
df = df.withColumn("risk_label", F.lit("UNKNOWN"))
