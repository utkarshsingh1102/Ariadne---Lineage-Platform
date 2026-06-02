"""JDBC MySQL via literal ``.option("url", ...)`` chain."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

df = (
    spark.read.format("jdbc")
    .option("url", "jdbc:mysql://mysql-prod.example.com:3306/inventory")
    .option("dbtable", "warehouse.items")
    .option("driver", "com.mysql.cj.jdbc.Driver")
    .option("user", "ro_user")
    .option("password", "leaked-if-not-stripped")
    .load()
)
