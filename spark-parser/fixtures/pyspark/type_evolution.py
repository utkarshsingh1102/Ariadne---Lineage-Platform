"""Cast chains — string → int → double. Each cast should appear in type_history."""
from pyspark.sql import SparkSession
from pyspark.sql.functions import col

spark = SparkSession.builder.getOrCreate()

raw = spark.table("prod.raw.orders")
enriched = (
    raw
    .withColumn("amount_int", col("amount").cast("int"))
    .withColumn("amount_double", col("amount_int").cast("double"))
)
enriched.write.format("delta").saveAsTable("prod.mart.orders_typed")
