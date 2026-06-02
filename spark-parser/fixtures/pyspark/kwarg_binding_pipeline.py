"""Regression fixture — interprocedural kwarg/default binding + dynamic sink
re-resolution after argument binding.

Exercises (in one file):
  - ``write_delta(df, path, partition_by=[…], z_order=…)`` — keyword args
    bound by name, partition columns flow onto the WriteEdge, z-order is
    captured. The default ``mode="overwrite"`` must NOT trigger
    interproc_args_mismatch.
  - ``read_jdbc(spark, table="public.customers")`` — the kwarg becomes the
    JDBC ``dbtable`` option after binding.
  - ``writer.save(path)`` where ``path`` was bound at the call site to
    ``cfg.GOLD_*`` — must resolve to the literal S3 URI, not emit
    ``dynamic_table_name``.
"""
from pyspark.sql import SparkSession


class PipelineConfig:
    PG_URL = "jdbc:postgresql://rds.example.com:5432/ecommerce"
    PG_DRIVER = "org.postgresql.Driver"
    # Bucket prefix referenced by sibling attrs through an f-string —
    # Python class-body scoping makes the bare ``BUCKET`` name resolve
    # to this attribute at class-definition time.
    BUCKET = "s3a://gold"
    GOLD_ORDERS = f"{BUCKET}/orders/"
    GOLD_CUSTOMERS = f"{BUCKET}/customers/"
    GOLD_RISK = f"{BUCKET}/risk/"
    GOLD_SEGMENT = f"{BUCKET}/segment/"


cfg = PipelineConfig()


def read_jdbc(spark, table, partition_col=None, num_partitions=None):
    return (
        spark.read.format("jdbc")
        .option("url", cfg.PG_URL)
        .option("driver", cfg.PG_DRIVER)
        .option("dbtable", table)
        .load()
    )


def write_delta(df, path, partition_by, mode="overwrite", z_order=None):
    writer = (
        df.write
        .format("delta")
        .mode(mode)
        .option("overwriteSchema", "true")
        .partitionBy(*partition_by)
    )
    writer.save(path)


spark = SparkSession.builder.appName("kwarg").getOrCreate()

df_orders = read_jdbc(spark, table="public.orders")
df_customers = read_jdbc(spark, table="public.customers")

write_delta(
    df_orders,
    cfg.GOLD_ORDERS,
    partition_by=["order_year", "order_month"],
    z_order="customer_id,product_id",
)
write_delta(
    df_customers,
    cfg.GOLD_CUSTOMERS,
    partition_by=["country_code"],
    z_order="customer_id",
)
