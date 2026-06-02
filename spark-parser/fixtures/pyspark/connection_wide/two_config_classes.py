"""Two distinct config classes in the same file."""
from pyspark.sql import SparkSession


class DbConfig:
    URL = "jdbc:postgresql://db.example.com:5432/main"


class LakeConfig:
    BUCKET = "s3a://prod-lake"


spark = SparkSession.builder.getOrCreate()
db_cfg = DbConfig()
lake_cfg = LakeConfig()

df1 = (
    spark.read.format("jdbc")
    .option("url", db_cfg.URL)
    .option("dbtable", "public.orders")
    .load()
)
df2 = spark.read.parquet(f"{lake_cfg.BUCKET}/bronze/orders/")
