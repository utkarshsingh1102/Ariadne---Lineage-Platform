"""S3 bucket with many read + many write paths — single Connection node."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

BUCKET = "s3a://ecom-data-lake"

# Several reads from the same bucket, different prefixes.
df1 = spark.read.parquet(f"{BUCKET}/bronze/orders/")
df2 = spark.read.parquet(f"{BUCKET}/bronze/customers/")
df3 = spark.read.csv(f"{BUCKET}/raw/returns.csv")

# Several writes to the same bucket.
df1.write.mode("overwrite").parquet(f"{BUCKET}/gold/fact_orders/")
df2.write.mode("overwrite").parquet(f"{BUCKET}/gold/dim_customers/")
df3.write.mode("overwrite").json(f"{BUCKET}/gold/returns.json")
