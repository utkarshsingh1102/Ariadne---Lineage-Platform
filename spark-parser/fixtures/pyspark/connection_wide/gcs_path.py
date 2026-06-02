"""GCS gs:// path."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
df = spark.read.json("gs://prod-bucket/raw/events/")
