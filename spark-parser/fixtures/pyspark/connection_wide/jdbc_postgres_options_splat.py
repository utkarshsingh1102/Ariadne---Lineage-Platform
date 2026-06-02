"""JDBC Postgres via ``.options(**dict)`` splat."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

pg_options = {
    "url": "jdbc:postgresql://rds-prod.example.com:5432/ecom",
    "dbtable": "public.orders",
    "driver": "org.postgresql.Driver",
    "user": "etl_user",
    "password": "should-never-appear",
}

df = spark.read.format("jdbc").options(**pg_options).load()
