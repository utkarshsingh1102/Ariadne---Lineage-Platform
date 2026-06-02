"""Cache + persist + checkpoint + repartition + coalesce — every hint kind."""
from pyspark.sql import SparkSession
from pyspark.sql.functions import col

spark = SparkSession.builder.getOrCreate()

cached = spark.table("prod.raw.orders").cache()
persisted = spark.table("prod.raw.events").persist("MEMORY_ONLY")
checkpointed = spark.table("prod.raw.users").checkpoint()
repartitioned = spark.table("prod.raw.orders").repartition(8, col("customer_id"))
coalesced = spark.table("prod.raw.orders").coalesce(2)

cached.write.format("delta").saveAsTable("prod.mart.cached_out")
persisted.write.format("delta").saveAsTable("prod.mart.persisted_out")
checkpointed.write.format("delta").saveAsTable("prod.mart.checkpointed_out")
repartitioned.write.format("delta").saveAsTable("prod.mart.repartitioned_out")
coalesced.write.format("delta").saveAsTable("prod.mart.coalesced_out")
