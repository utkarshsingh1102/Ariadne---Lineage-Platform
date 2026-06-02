"""Cycle: b imports a, completing the loop."""
from a import do_a  # noqa: F401


def do_b(df):
    return df.withColumn("b_marker", df.id)
