"""Dict-based config: ``CONFIG["pg_url"]`` lookup."""
from pyspark.sql import SparkSession

CONFIG = {
    "pg_url": "jdbc:postgresql://config-dict.example.com:5432/ecom",
    "pg_dbtable": "public.events",
}

spark = SparkSession.builder.getOrCreate()
df = (
    spark.read.format("jdbc")
    .option("url", CONFIG["pg_url"])
    .option("dbtable", CONFIG["pg_dbtable"])
    .load()
)
