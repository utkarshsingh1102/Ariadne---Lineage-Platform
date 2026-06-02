# Fixture 07 — Dynamic table name resolution test
# Plan §14: if the env can be resolved statically, lineage is complete.
# If it can't (sys.argv, dbutils.widgets), mark lineage_partial=true with warning.

import sys
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("dyn_etl").getOrCreate()

# --- Static — should resolve to prod.dim.customers ---
env = "prod"
table_static = f"{env}.dim.customers"
customers = spark.table(table_static)

# --- Dynamic — comes from CLI argv; cannot be resolved at parse time ---
target = sys.argv[1]
customers.write.format("delta").mode("overwrite").saveAsTable(target)
