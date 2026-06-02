"""Dynamic binding via setattr / locals()[…]."""
import os

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

names = ["orders", "events"]


class Holder:
    pass


h = Holder()
for n in names:
    # setattr with a non-constant attribute name — runtime_dynamic / setattr.
    setattr(h, n, spark.table(f"prod.raw.{n}"))

# locals()[...] = ...
locals()["dynamic_var"] = spark.table("prod.raw.something")  # dynamic_binding
