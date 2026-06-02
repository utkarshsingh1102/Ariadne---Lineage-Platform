"""
Notebook extraction (plan §2.3 / §6 step 3).
Extract code cells in order; track cell language.
"""
import pytest


def test_jupyter_extracts_python_cells_in_order(notebook_fixture):
    from spark_parser.input.notebook import extract_cells
    cells = extract_cells(notebook_fixture("01_simple.ipynb"))
    py_cells = [c for c in cells if c.language == "python"]
    assert len(py_cells) == 2
    # Order preserved
    assert "SparkSession" in py_cells[0].source
    assert "saveAsTable" in py_cells[1].source


def test_jupyter_skips_markdown_cells(notebook_fixture):
    from spark_parser.input.notebook import extract_cells
    cells = extract_cells(notebook_fixture("01_simple.ipynb"))
    # No markdown cells in the output
    assert all(c.language != "markdown" for c in cells)


def test_databricks_py_cells_split_by_separator(notebook_fixture):
    from spark_parser.input.notebook import extract_cells
    cells = extract_cells(notebook_fixture("02_databricks_format.py"))
    # The fixture has 4 code cells separated by `# COMMAND ----------`
    assert len(cells) == 4
    assert all(c.language == "python" for c in cells)


def test_mixed_notebook_classifies_sql_cells(notebook_fixture):
    """Plan §6 step 3: SQL cells must be routed to the SQL path, Python cells
    to the Python path."""
    from spark_parser.input.notebook import extract_cells
    cells = extract_cells(notebook_fixture("03_mixed_python_sql.ipynb"))
    by_lang = {c.language for c in cells}
    assert "python" in by_lang
    assert "sql" in by_lang
    sql_cells = [c for c in cells if c.language == "sql"]
    assert len(sql_cells) == 2
    assert "CREATE OR REPLACE TABLE" in sql_cells[0].source


def test_python_cells_concatenated_in_order(notebook_fixture):
    """The visitor walks ONE concatenated Python module per notebook —
    variables defined in cell 1 must be visible in cell 2."""
    from spark_parser.input.notebook import concatenate_python_cells
    src = concatenate_python_cells(notebook_fixture("01_simple.ipynb"))
    # The concatenated source must contain BOTH cells' content
    assert "SparkSession" in src
    assert "saveAsTable" in src
    # And in the right order
    assert src.index("SparkSession") < src.index("saveAsTable")


def test_dbc_archive_unpacks_to_notebook(tmp_path):
    """Plan §2.3: .dbc is a ZIP containing notebook JSON."""
    import json
    import zipfile
    from spark_parser.input.notebook import extract_cells

    dbc = tmp_path / "demo.dbc"
    nb_json = {
        "cells": [{
            "cell_type": "code",
            "source": ["spark.read.parquet('s3://a/')"],
            "metadata": {},
        }],
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"language_info": {"name": "python"}},
    }
    with zipfile.ZipFile(dbc, "w") as zf:
        zf.writestr("notebook.ipynb", json.dumps(nb_json))

    cells = extract_cells(dbc)
    assert len(cells) == 1
    assert "spark.read.parquet" in cells[0].source
