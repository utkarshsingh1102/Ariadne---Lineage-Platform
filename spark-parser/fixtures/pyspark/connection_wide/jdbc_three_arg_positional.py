"""3-arg positional JDBC: ``spark.read.jdbc(url, table, properties)``."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

props = {"user": "ro", "password": "should-not-leak"}

df = spark.read.jdbc(
    "jdbc:postgresql://rds-prod.example.com:5432/ecom",
    "public.events",
    properties=props,
)

# Symmetric write path: df.write.jdbc(url, table, mode, properties).
df.write.jdbc(
    "jdbc:postgresql://rds-prod.example.com:5432/ecom",
    "public.events_copy",
    mode="overwrite",
    properties=props,
)
