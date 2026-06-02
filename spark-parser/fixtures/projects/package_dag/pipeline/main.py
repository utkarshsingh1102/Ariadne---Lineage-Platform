"""Two-hop import chain: main → transforms → utils."""
from pyspark.sql import SparkSession

from .transforms import enrich

spark = SparkSession.builder.appName("package_dag").getOrCreate()

raw = spark.read.format("parquet").load("s3://raw/events/")
enriched = enrich(raw)
enriched.write.format("delta").mode("overwrite").saveAsTable("prod.mart.events_enriched")
