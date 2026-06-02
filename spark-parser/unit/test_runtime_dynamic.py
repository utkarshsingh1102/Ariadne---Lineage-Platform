"""Unit tests for v0.2 §3 — runtime-dynamic detection."""
from __future__ import annotations

from spark_parser.pyspark.visitor import parse_pyspark


def _warnings_of(ir, subtype: str):
    return [w for w in ir.warnings if w.type == "runtime_dynamic" and w.subtype == subtype]


def test_eval_emits_warning(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("eval_transform.py"))
    matches = _warnings_of(ir, "eval")
    assert matches, "expected at least one runtime_dynamic/eval warning"
    assert all(df.lineage_partial for df in ir.dataframes), \
        "every DataFrame should be marked partial when eval is present"


def test_sql_template_emits_warning(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("sql_template_runtime.py"))
    assert _warnings_of(ir, "sql_template"), \
        "expected a sql_template warning for the f-string with sys.argv"


def test_reflection_emits_warning(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("reflection_pipeline.py"))
    assert _warnings_of(ir, "reflection"), \
        "getattr() with a non-constant attribute name should warn"


def test_setattr_emits_warning(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("setattr_dataframes.py"))
    assert _warnings_of(ir, "setattr"), \
        "setattr() with a non-constant attribute should warn"
    assert _warnings_of(ir, "dynamic_binding"), \
        "locals()[…] = … should warn as dynamic_binding"


def test_literal_loop_does_not_warn(pyspark_fixture):
    """No dynamic_loop warning when the iterable is a literal list."""
    ir = parse_pyspark(pyspark_fixture("literal_loop_lineage.py"))
    assert not _warnings_of(ir, "dynamic_loop")


def test_dynamic_loop_warns_when_iterable_is_runtime(pyspark_fixture):
    ir = parse_pyspark(pyspark_fixture("dynamic_loop_runtime.py"))
    assert _warnings_of(ir, "dynamic_loop")


def test_no_runtime_dynamic_on_static_pipeline(pyspark_fixture):
    """Sanity: a v0.1 static fixture must not emit any runtime_dynamic warning."""
    ir = parse_pyspark(pyspark_fixture("01_simple_read_write.py"))
    runtime_dyn = [w for w in ir.warnings if w.type == "runtime_dynamic"]
    assert runtime_dyn == []
