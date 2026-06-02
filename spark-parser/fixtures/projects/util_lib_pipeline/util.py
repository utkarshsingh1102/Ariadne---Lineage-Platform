"""Shared transformation helper imported by ``entry.py``.

The function takes a DataFrame in and returns a transformed one — pure
DataFrame-API code so the cross-file lineage stitch is unambiguous.
"""
from pyspark.sql.functions import col


def enrich(orders):
    return (
        orders
        .withColumn("region_upper", col("region").cast("string"))
        .withColumn("amount_doubled", col("amount") * 2)
    )
