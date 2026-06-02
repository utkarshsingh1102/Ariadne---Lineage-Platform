# Fixture 05 — union / unionByName
# A combined DataFrame derived from two parents.
# Plan §6 step 6.

from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("union_etl").getOrCreate()

current = spark.read.format("parquet").load("s3://raw/orders/")
archive = spark.read.format("parquet").load("s3://archive/orders/")

combined = current.unionByName(archive, allowMissingColumns=True)

deduped = combined.dropDuplicates(["order_id"])

deduped.write.format("delta").mode("overwrite").saveAsTable("prod.mart.orders_all")
