"""Reader stored in a variable; options added across multiple statements."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

r = spark.read.format("jdbc")
r.option("url", "jdbc:postgresql://shared.example.com:5432/etl")
r.option("dbtable", "raw.events")
r.option("driver", "org.postgresql.Driver")
df = r.load()
