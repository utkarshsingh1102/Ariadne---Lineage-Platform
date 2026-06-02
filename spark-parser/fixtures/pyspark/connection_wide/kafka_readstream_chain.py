"""Kafka via readStream — structured streaming source."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

df = (
    spark.readStream
    .format("kafka")
    .option("kafka.bootstrap.servers", "kafka-1.example.com:9092,kafka-2.example.com:9092")
    .option("subscribe", "order_events")
    .option("startingOffsets", "earliest")
    .load()
)

# Symmetric writeStream sink.
(
    df.writeStream
    .format("kafka")
    .option("kafka.bootstrap.servers", "kafka-1.example.com:9092,kafka-2.example.com:9092")
    .option("topic", "order_events_enriched")
    .outputMode("append")
    .start()
)
