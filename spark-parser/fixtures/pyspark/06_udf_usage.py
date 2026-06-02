# Fixture 06 — UDF and pandas_udf detection
# Asserts USES_UDF edges and UDF nodes.
# Plan §2.4 + §6 step 6 (UDF body NOT introspected).

from pyspark.sql import SparkSession
from pyspark.sql.functions import udf, pandas_udf, col
from pyspark.sql.types import StringType, DoubleType

spark = SparkSession.builder.appName("udf_etl").getOrCreate()


@udf(returnType=StringType())
def normalise_region(region: str) -> str:
    if region is None:
        return "UNKNOWN"
    return region.strip().upper()


@pandas_udf(DoubleType())
def amount_to_eur(amount_series, fx_series):
    return amount_series * fx_series


orders = spark.read.format("parquet").load("s3://raw/orders/")

transformed = (
    orders
    .withColumn("region_norm", normalise_region(col("region")))
    .withColumn("amount_eur", amount_to_eur(col("amount"), col("fx_rate")))
)

transformed.write.format("delta").mode("overwrite").saveAsTable("prod.mart.orders_norm")
