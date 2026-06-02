"""Same DB with explicit port and without — default-port fill must dedup."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

df1 = (
    spark.read.format("jdbc")
    .option("url", "jdbc:postgresql://shared.example.com:5432/etl")
    .option("dbtable", "raw.a")
    .load()
)
df2 = (
    spark.read.format("jdbc")
    .option("url", "jdbc:postgresql://shared.example.com/etl")
    .option("dbtable", "raw.b")
    .load()
)
