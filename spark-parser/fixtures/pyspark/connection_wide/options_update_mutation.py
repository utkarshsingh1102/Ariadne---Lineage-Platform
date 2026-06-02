"""Options dict mutated via .update({...}) and subscript before load."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

options = {"driver": "org.postgresql.Driver"}
options.update({
    "url": "jdbc:postgresql://shared.example.com:5432/etl",
    "dbtable": "raw.events",
})
options["user"] = "etl"

df = spark.read.format("jdbc").options(**options).load()
