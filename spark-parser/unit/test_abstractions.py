"""Unit tests for v0.2 §8 — procedural abstractions."""
from __future__ import annotations

from spark_parser.pyspark.visitor import parse_pyspark


def _all_derivations(ir) -> set[str]:
    return {d.target_column for df in ir.dataframes for d in df.derivations}


def test_class_method_inlines_into_caller(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("class_pipeline.py"))
    derived = _all_derivations(ir)
    # `enrich` adds "revenue" via withColumn — visitor must thread it across
    # the instance method call.
    assert "revenue" in derived
    # And the final DataFrame links back to the class method.
    enriched = next(df for df in ir.dataframes if df.var_name == "enriched")
    edge_vias = {e.via for e in enriched.derives_from_dataframe}
    assert "class_method" in edge_vias


def test_hof_factory_closure_inlines(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("hof_factory_pipeline.py"))
    derived = _all_derivations(ir)
    # Inner closure called withColumn("region_upper", ...) — should land.
    assert "region_upper" in derived


def test_transform_with_local_callback_inlines(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("callback_transforms.py"))
    derived = _all_derivations(ir)
    # `add_revenue` adds "revenue" — `.transform(add_revenue)` should pick it up.
    assert "revenue" in derived


def test_transform_with_external_callback_warns(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("callback_transforms.py"))
    matches = [w for w in ir.warnings if w.type == "external_callback"]
    assert matches, "expected an external_callback warning for some_external_fn"
