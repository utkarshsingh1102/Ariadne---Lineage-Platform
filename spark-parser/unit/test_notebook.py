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


# ---------------------------------------------------------------------------
# Jupyter magic stripping — regression coverage for the spark-parser bug
# where `!pip install` (or any line magic) at the top of a notebook caused
# ast.parse() to fail across the entire file and emit zero stats.
# ---------------------------------------------------------------------------

def _ipynb_with(cells_src: list[str]) -> dict:
    return {
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {"cell_type": "code", "source": s, "execution_count": i + 1}
            for i, s in enumerate(cells_src)
        ],
        "nbformat": 4, "nbformat_minor": 5,
    }


def test_strip_shell_escape_drops_pip_install():
    from spark_parser.input.notebook import _cells_from_ipynb_dict
    cells = _cells_from_ipynb_dict(_ipynb_with([
        "!pip install pyspark",
        "from pyspark.sql import SparkSession\nspark = SparkSession.builder.getOrCreate()",
    ]))
    # First cell was nothing but a shell escape — should be dropped entirely.
    assert len(cells) == 1
    assert "SparkSession" in cells[0].source


def test_strip_line_magic_keeps_rest_of_cell():
    from spark_parser.input.notebook import _cells_from_ipynb_dict
    cells = _cells_from_ipynb_dict(_ipynb_with([
        "%matplotlib inline\nimport pandas as pd",
    ]))
    assert len(cells) == 1
    assert "import pandas as pd" in cells[0].source
    assert "%matplotlib" not in cells[0].source


def test_sql_cell_magic_reclassifies_cell_as_sql():
    from spark_parser.input.notebook import _cells_from_ipynb_dict
    cells = _cells_from_ipynb_dict(_ipynb_with([
        "%%sql\nSELECT * FROM movies WHERE rating > 4",
    ]))
    assert len(cells) == 1
    assert cells[0].language == "sql"
    assert "SELECT * FROM movies" in cells[0].source
    assert "%%sql" not in cells[0].source


def test_bash_cell_magic_drops_cell_entirely():
    from spark_parser.input.notebook import _cells_from_ipynb_dict
    cells = _cells_from_ipynb_dict(_ipynb_with([
        "%%bash\nls -la /tmp",
        "from pyspark.sql import SparkSession",
    ]))
    # %%bash body isn't Python and isn't SQL — drop it; keep the SparkSession cell.
    assert len(cells) == 1
    assert "SparkSession" in cells[0].source


def test_timeit_cell_magic_keeps_python_body():
    from spark_parser.input.notebook import _cells_from_ipynb_dict
    cells = _cells_from_ipynb_dict(_ipynb_with([
        "%%timeit\ndf.count()",
    ]))
    assert len(cells) == 1
    assert cells[0].language == "python"
    assert "df.count()" in cells[0].source


def test_real_world_netflix_notebook_parses_without_syntax_error():
    """The exact case the user hit: !pip install at the top of a notebook
    caused zero stats and a 'invalid syntax' warning. Concatenated Python
    must now ast.parse cleanly."""
    import ast
    from spark_parser.input.notebook import _cells_from_ipynb_dict
    cells = _cells_from_ipynb_dict(_ipynb_with([
        "!pip install pyspark",
        "from pyspark.sql import SparkSession\nspark = SparkSession.builder.getOrCreate()",
        'movies = spark.read.format("csv").option("header", "true").load("netflix_titles.csv")',
        "df = movies.select('title', 'release_year', 'country', 'rating')",
        "df2 = df.withColumn('year', df['release_year'].cast('int')).drop('release_year')",
    ]))
    py_src = "\n\n".join(c.source for c in cells if c.language == "python")
    ast.parse(py_src)  # would raise SyntaxError before the fix
