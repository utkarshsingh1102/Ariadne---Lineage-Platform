"""Two shadowing patterns: select duplicate alias + withColumn overwrite.

The overwrite case chains TWO ``withColumn`` calls on the same name so the
overwrite is structurally visible (the visitor doesn't introspect table
schemas, so single-shot overwrites on raw reads can't be detected statically).
"""
from pyspark.sql import SparkSession
from pyspark.sql.functions import col

spark = SparkSession.builder.getOrCreate()

raw = spark.table("prod.raw.orders")
proj = raw.select("order_id", "amount", "amount")        # duplicate alias
overwritten = (
    raw
    .withColumn("amount_x2", col("amount") * 2)
    .withColumn("amount_x2", col("amount") * 4)          # overwrite "amount_x2"
)
overwritten.write.format("delta").saveAsTable("prod.mart.orders_shadow")
