# Databricks notebook source

# MAGIC %run ./helpers/setup

# COMMAND ----------

from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
df = spark.read.format("parquet").load("s3://raw/events/")

# COMMAND ----------

# MAGIC %run ./helpers/finalize

# COMMAND ----------

df.write.format("delta").saveAsTable("prod.mart.events_out")
