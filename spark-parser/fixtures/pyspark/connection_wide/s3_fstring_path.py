"""S3 path constructed via f-string against module-level constants."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

BUCKET = "s3a://ecom-data-lake"
LAYER = "bronze"

df = spark.read.parquet(f"{BUCKET}/{LAYER}/orders/")
df.write.mode("overwrite").parquet(f"{BUCKET}/gold/orders/")
