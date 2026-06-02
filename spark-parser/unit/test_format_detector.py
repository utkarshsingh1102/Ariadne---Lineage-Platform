"""
Input-format detection (plan §6 step 1).
.py / .sql / .ipynb / .dbc / .scala plus content-sniff for Databricks .py.
"""
import pytest


def test_pyspark_file_detected(pyspark_fixture):
    from spark_parser.input.format_detector import detect_format
    assert detect_format(pyspark_fixture("01_simple_read_write.py")) == "pyspark"


def test_sparksql_file_detected(sparksql_fixture):
    from spark_parser.input.format_detector import detect_format
    assert detect_format(sparksql_fixture("01_simple_ctas.sql")) == "sparksql"


def test_jupyter_notebook_detected(notebook_fixture):
    from spark_parser.input.format_detector import detect_format
    assert detect_format(notebook_fixture("01_simple.ipynb")) == "notebook_jupyter"


def test_databricks_py_detected_by_content(notebook_fixture):
    """A .py file with `# Databricks notebook source` header must NOT be
    classified as plain PySpark."""
    from spark_parser.input.format_detector import detect_format
    assert detect_format(notebook_fixture("02_databricks_format.py")) == "notebook_databricks"


def test_dbc_detected(tmp_path):
    """.dbc files are ZIP archives — detected by extension."""
    from spark_parser.input.format_detector import detect_format
    dbc = tmp_path / "test.dbc"
    dbc.write_bytes(b"PK\x03\x04")  # ZIP magic
    assert detect_format(dbc) == "notebook_databricks_archive"


def test_scala_skipped_with_warning(tmp_path):
    """Plan §2.4 + §14: Scala is out of scope for v0.1 — return 'scala' so the
    pipeline can log a warning and exit, NOT raise."""
    from spark_parser.input.format_detector import detect_format
    sc = tmp_path / "Main.scala"
    sc.write_text("object Main { def main(args: Array[String]): Unit = {} }")
    assert detect_format(sc) == "scala"


def test_unknown_extension_raises(tmp_path):
    from spark_parser.input.format_detector import detect_format, FormatDetectionError
    p = tmp_path / "weird.xyz"
    p.write_text("???")
    with pytest.raises(FormatDetectionError):
        detect_format(p)
