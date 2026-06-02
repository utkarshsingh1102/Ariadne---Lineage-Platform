"""Entry point that uses a relative import."""
from pyspark.sql import SparkSession

from .transforms import clean

spark = SparkSession.builder.appName("rel_imports").getOrCreate()

raw = spark.read.format("parquet").load("s3://raw/events/")
cleaned = clean(raw)
cleaned.write.format("delta").mode("overwrite").saveAsTable("prod.mart.events_clean")
