"""withColumn with a dotted (nested-struct) path."""
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, upper

spark = SparkSession.builder.getOrCreate()

users = spark.table("prod.dim.users")
enriched = (
    users
    .withColumn("address.city_upper", upper(col("address.city")))
    .withColumn("profile.contact.email_domain", col("profile.contact.email"))
)
enriched.write.format("delta").saveAsTable("prod.mart.users_enriched")
