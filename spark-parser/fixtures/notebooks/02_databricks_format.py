# Databricks notebook source
# MAGIC %md
# MAGIC # Databricks-format notebook fixture
# MAGIC Tests the `# Databricks notebook source` + `# COMMAND ----------` cell separators.
# MAGIC Plan §2.3.

# COMMAND ----------
from pyspark.sql import SparkSession
from pyspark.sql.functions import col

spark = SparkSession.builder.appName("dbx_nb").getOrCreate()

# COMMAND ----------
# A widget would normally be used here; treat as dynamic (plan §14)
target_table = dbutils.widgets.get("target_table")  # noqa: F821 — dbutils provided by Databricks runtime

# COMMAND ----------
orders = spark.read.format("delta").load("abfss://lake@acct/orders/")

filtered = orders.filter(col("status") == "CONFIRMED")

# COMMAND ----------
filtered.write.format("delta").mode("overwrite").saveAsTable(target_table)
