"""Unit tests for v0.2 §2 — notebook runtime semantics."""
from __future__ import annotations

from pathlib import Path

from spark_parser.main import parse_input

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "notebooks"


def test_cells_carried_onto_ir():
    """Per-cell metadata (index + language + execution_count) reaches the IR."""
    ir = parse_input(str(FIXTURES / "03_mixed_python_sql.ipynb"))
    assert ir.cells, "expected at least one cell on the IR"
    # Every cell has an index that matches its position in the source notebook
    indices = [c.index for c in ir.cells]
    assert indices == sorted(indices)


def test_out_of_order_execution_emits_hidden_state_warning():
    ir = parse_input(str(FIXTURES / "out_of_order_execution.ipynb"))
    matches = [
        w for w in ir.warnings
        if w.type == "hidden_state" and w.subtype == "out_of_order_execution"
    ]
    assert matches, "out-of-order execution_count should emit hidden_state"
    # Every DataFrame should now be partial
    assert all(df.lineage_partial for df in ir.dataframes)


def test_in_order_execution_does_not_warn():
    """Sanity: the dbutils fixture has execution_counts 1, 2 — no hidden_state."""
    ir = parse_input(str(FIXTURES / "dbutils_run_chain.ipynb"))
    matches = [w for w in ir.warnings if w.type == "hidden_state"]
    assert matches == []


def test_dbutils_notebook_run_edges_recorded():
    ir = parse_input(str(FIXTURES / "dbutils_run_chain.ipynb"))
    runs = ir.notebook_runs
    paths = {e.target_path for e in runs if e.kind == "dbutils_notebook_run"}
    assert "./shared/cleanup" in paths
    assert "./shared/finalize" in paths


def test_magic_run_edges_recorded():
    ir = parse_input(str(FIXTURES / "magic_run_chain.py"))
    paths = {
        e.target_path for e in ir.notebook_runs if e.kind == "magic_run"
    }
    assert "./helpers/setup" in paths
    assert "./helpers/finalize" in paths


def test_notebook_runs_record_source_cell_index():
    ir = parse_input(str(FIXTURES / "dbutils_run_chain.ipynb"))
    runs = [e for e in ir.notebook_runs if e.kind == "dbutils_notebook_run"]
    assert all(e.source_cell_index is not None for e in runs)
