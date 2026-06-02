"""M2a/M2b fixture — recursive helpers + variadic args.

`looper` calls itself directly — must produce a `recursive_function`
warning and not blow the stack.

`stacker` takes `*dfs` — must produce an `interproc_vararg` warning and
bind only what fits the explicit args.
"""
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("recursion").getOrCreate()


def looper(df):
    """Direct self-recursion — analyser must refuse to inline."""
    return looper(df.dropDuplicates())


def stacker(*dfs):
    """Variadic — should warn, not silently swallow."""
    first = dfs[0] if dfs else None
    return first.filter("country IS NOT NULL") if first is not None else None


orders = spark.read.table("bronze.orders")
customers = spark.read.table("bronze.customers")

loop_out = looper(orders)
stacked = stacker(orders, customers)
