"""Kafka cluster referenced with two broker orderings — must dedup to 1 node."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

stream_a = (
    spark.readStream.format("kafka")
    .option("kafka.bootstrap.servers", "broker-1.example.com:9092,broker-2.example.com:9092")
    .option("subscribe", "events.a")
    .load()
)

stream_b = (
    spark.readStream.format("kafka")
    .option("kafka.bootstrap.servers", "broker-2.example.com:9092,broker-1.example.com:9092")
    .option("subscribe", "events.b")
    .load()
)
