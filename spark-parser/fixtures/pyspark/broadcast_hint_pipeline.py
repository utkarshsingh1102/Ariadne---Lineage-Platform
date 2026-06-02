"""broadcast(df) inside a join and .hint("broadcast") on the left side."""
from pyspark.sql import SparkSession
from pyspark.sql.functions import broadcast

spark = SparkSession.builder.getOrCreate()

orders = spark.table("prod.raw.orders")
customers = spark.table("prod.dim.customers")

# broadcast(...) wrapper
out_a = orders.join(broadcast(customers), orders.customer_id == customers.id, "left")
out_a.write.format("delta").saveAsTable("prod.mart.orders_bcast_a")

# df.hint("broadcast") variant
hinted = customers.hint("broadcast")
out_b = orders.join(hinted, orders.customer_id == hinted.id, "left")
out_b.write.format("delta").saveAsTable("prod.mart.orders_bcast_b")
