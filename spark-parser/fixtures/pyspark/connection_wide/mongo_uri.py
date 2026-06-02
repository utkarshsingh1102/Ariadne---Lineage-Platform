"""MongoDB via spark.mongodb.read.connection.uri."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

df = (
    spark.read.format("mongo")
    .option("spark.mongodb.read.connection.uri", "mongodb://app:reddit-leak@mongo-prod.example.com:27017/ecom")
    .option("collection", "orders")
    .load()
)
