"""Same DB written as ``localhost`` and ``127.0.0.1`` — must dedup to one node."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

df1 = (
    spark.read.format("jdbc")
    .option("url", "jdbc:postgresql://localhost:5432/etl")
    .option("dbtable", "raw.a")
    .load()
)
df2 = (
    spark.read.format("jdbc")
    .option("url", "jdbc:postgresql://127.0.0.1:5432/etl")
    .option("dbtable", "raw.b")
    .load()
)
