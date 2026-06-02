"""Reads + writes across all six v0.2 external connectors.

Each section uses the most common idiom for the connector — verified against
the public docs at the time of writing. Lineage should produce canonical FQNs
(``kafka://servers/topic``, ``snowflake://<account>``, etc.) so cross-parser
:Table merging continues to work.
"""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

# --- Kafka ----------------------------------------------------------------
kafka_in = (
    spark.read.format("kafka")
        .option("kafka.bootstrap.servers", "broker:9092")
        .option("subscribe", "orders")
        .load()
)

# --- Iceberg --------------------------------------------------------------
iceberg_in = spark.read.format("iceberg").load("hive_prod.db.events_v2")

# --- Hudi -----------------------------------------------------------------
hudi_in = spark.read.format("hudi").load("s3://datalake/hudi/users")

# --- Snowflake ------------------------------------------------------------
snow_in = (
    spark.read.format("snowflake")
        .option("sfUrl", "ab12345.snowflakecomputing.com")
        .option("sfDatabase", "PROD")
        .option("sfSchema", "DIM")
        .option("dbtable", "CUSTOMERS")
        .load()
)

# --- BigQuery -------------------------------------------------------------
bq_in = (
    spark.read.format("bigquery")
        .option("table", "myproj.dataset.orders")
        .load()
)

# --- Redshift -------------------------------------------------------------
rs_in = (
    spark.read.format("redshift")
        .option("url", "jdbc:redshift://rs.example.com:5439/prod")
        .option("dbtable", "public.events")
        .load()
)

# --- Writes ---------------------------------------------------------------
kafka_in.write.format("kafka").option("kafka.bootstrap.servers", "broker:9092").option("topic", "orders_out").save()
iceberg_in.write.format("iceberg").mode("append").save("hive_prod.db.events_archive")
snow_in.write.format("snowflake").option("sfUrl", "ab12345.snowflakecomputing.com").option("sfDatabase", "PROD").option("sfSchema", "MART").option("dbtable", "CUSTOMERS_OUT").save()
