"""Unknown URI scheme ``foo://bar/baz`` — must still produce a Connection node."""
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

df = spark.read.format("parquet").load("foo://prod-cluster/datasets/x/")
