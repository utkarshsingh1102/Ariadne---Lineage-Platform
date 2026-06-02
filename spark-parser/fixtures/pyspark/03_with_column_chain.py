# Fixture 03 — withColumn / cast / when / lit chain
# Asserts DERIVES_FROM edges for transformed columns.
# Plan §2.1 + §6 step 7.

from pyspark.sql import SparkSession
from pyspark.sql.functions import col, lit, when

spark = SparkSession.builder.appName("withcol_etl").getOrCreate()

orders = spark.read.format("parquet").load("s3://raw/orders/")

transformed = (
    orders
    .withColumn("region_upper", col("region").cast("string"))
    .withColumn("amount_with_tax", col("amount") * lit(1.18))
    .withColumn("is_high_value", when(col("amount") > 1000, lit(True)).otherwise(lit(False)))
    .withColumnRenamed("order_id", "id")
    .drop("internal_flag")
)

transformed.write.format("delta").mode("overwrite").saveAsTable("prod.mart.orders_transformed")
