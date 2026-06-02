"""
UDF and pandas_udf detection (plan §2.4 + §6).
The parser captures inputs/outputs at the call site; it does NOT introspect
the function body (out of scope for v0.1).
"""
import pytest


def test_udf_node_created(pyspark_fixture):
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("06_udf_usage.py")))
    udf_names = {u.name for u in ir.udfs}
    assert "normalise_region" in udf_names
    assert "amount_to_eur" in udf_names


def test_pandas_udf_distinguished(pyspark_fixture):
    """pandas_udf should be flagged distinctly from regular udf."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("06_udf_usage.py")))
    pandas = next(u for u in ir.udfs if u.name == "amount_to_eur")
    assert pandas.is_pandas_udf is True
    regular = next(u for u in ir.udfs if u.name == "normalise_region")
    assert regular.is_pandas_udf is False


def test_udf_call_emits_uses_udf_edge(pyspark_fixture):
    """`region_norm = normalise_region(col('region'))` →
    region_norm DERIVES_FROM region via='udf' + USES_UDF edge."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("06_udf_usage.py")))

    derives = [d for df in ir.dataframes for d in df.derivations]
    region_norm = next(d for d in derives if d.target_column == "region_norm")
    assert region_norm.via == "udf"
    assert "region" in region_norm.source_columns


def test_pandas_udf_call_captures_both_inputs(pyspark_fixture):
    """`amount_eur = amount_to_eur(col('amount'), col('fx_rate'))` →
    both inputs flow into amount_eur."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("06_udf_usage.py")))

    derives = [d for df in ir.dataframes for d in df.derivations]
    amount_eur = next(d for d in derives if d.target_column == "amount_eur")
    assert set(amount_eur.source_columns) == {"amount", "fx_rate"}


def test_udf_return_type_captured(pyspark_fixture):
    """@udf(returnType=StringType()) → return_type='string'."""
    from spark_parser.pyspark.visitor import parse_pyspark
    ir = parse_pyspark(str(pyspark_fixture("06_udf_usage.py")))
    nr = next(u for u in ir.udfs if u.name == "normalise_region")
    assert (nr.return_type or "").lower() in {"string", "stringtype()"}
