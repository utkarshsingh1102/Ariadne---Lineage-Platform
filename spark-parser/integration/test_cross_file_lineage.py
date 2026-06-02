"""End-to-end: lineage threads through a cross-file function call (v0.2 §1).

These tests exercise ``ProjectParser`` against the ``fixtures/projects/`` tree
and assert that a column added by an imported helper shows up on the entry
script's terminal DataFrame, and that the entry's source/target tables are
correctly recovered even though the join / withColumn lives in another file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from spark_parser.project.project_parser import parse_project

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "projects"


def _entry_module(project, file_suffix: str):
    return next(m for m in project.modules if m.file_path.endswith(file_suffix))


def _all_table_names(module) -> set[str]:
    out: set[str] = set()
    for df in module.dataframes:
        for t in df.reads_from:
            if t.fully_qualified_name:
                out.add(t.fully_qualified_name)
            elif t.location:
                out.add(t.location)
        for t in df.writes_to:
            if t.fully_qualified_name:
                out.add(t.fully_qualified_name)
            elif t.location:
                out.add(t.location)
    return out


def _all_derivations(module) -> set[str]:
    """Collect derivation target columns across every DataFrame in the module.

    The visitor splits chain steps into intermediate DataFrames (v0.1 design),
    so a cross-file test must inspect the whole module to see every derived
    column the inlined function produced.
    """
    return {
        d.target_column
        for df in module.dataframes for d in df.derivations
    }


def test_util_lib_pipeline_inlines_enrich_into_entry() -> None:
    """``enrich(orders)`` lives in util.py — the entry module should carry
    both columns added inside ``enrich`` (``region_upper``, ``amount_doubled``)
    across its chain of intermediate DataFrames after cross-file inlining.
    """
    root = FIXTURES / "util_lib_pipeline"
    project = parse_project(root / "entry.py", project_root=root)

    entry = _entry_module(project, "entry.py")
    derived = _all_derivations(entry)
    assert "region_upper" in derived
    assert "amount_doubled" in derived

    # And the final DataFrame is correctly linked back to the imported helper.
    enriched = next(df for df in entry.dataframes if df.var_name == "enriched")
    edge_vias = {e.via for e in enriched.derives_from_dataframe}
    assert "cross_module_function" in edge_vias


def test_util_lib_pipeline_keeps_source_and_target_tables() -> None:
    """Source and target tables are still discoverable on the entry module."""
    root = FIXTURES / "util_lib_pipeline"
    project = parse_project(root / "entry.py", project_root=root)
    entry = _entry_module(project, "entry.py")
    tables = _all_table_names(entry)
    # Source path-style; target Hive table on saveAsTable
    assert "s3://raw/orders/" in tables
    assert "prod.mart.orders_enriched" in tables


def test_relative_imports_cross_file_lineage() -> None:
    """`from .transforms import clean` — `clean(raw)` adds a column."""
    root = FIXTURES / "relative_imports"
    project = parse_project(root / "pkg" / "main.py", project_root=root)
    entry = _entry_module(project, "pkg/main.py")
    derived = _all_derivations(entry)
    assert "upper_name" in derived


def test_package_dag_one_hop_inlines_enrich() -> None:
    """One-hop: ``main`` imports ``enrich`` from ``transforms``. The visitor
    inlines ``enrich``, but the chain after the inlined call (``...
    .withColumn(...)``) is not re-walked by the current ``_eval_call``
    dispatcher when ``chain[0]`` is itself a ``Call``. This is a v0.1 limit
    in ``_eval_call``, not a v0.2 regression. Assert the cross-module edge
    is recorded; chain-after-call resolution is tracked as a follow-up.
    """
    root = FIXTURES / "package_dag"
    project = parse_project(root / "pipeline" / "main.py", project_root=root)
    entry = _entry_module(project, "pipeline/main.py")
    enriched = next(df for df in entry.dataframes if df.var_name == "enriched")
    edge_vias = {e.via for e in enriched.derives_from_dataframe}
    assert "cross_module_function" in edge_vias


@pytest.mark.xfail(
    reason="Two-hop nested inlining: when an inlined function calls another "
           "imported function, the visitor only sees the caller-module's "
           "external_functions table. Phase 1 scope is one-hop; tracked for "
           "a follow-up.",
    strict=True,
)
def test_package_dag_two_hop_lineage() -> None:
    root = FIXTURES / "package_dag"
    project = parse_project(root / "pipeline" / "main.py", project_root=root)
    entry = _entry_module(project, "pipeline/main.py")
    derived = _all_derivations(entry)
    assert "ingested_at" in derived  # added by utils.add_metadata, 2 hops down


def test_cyclic_imports_do_not_loop_forever() -> None:
    """Both files appear once; no exception raised."""
    root = FIXTURES / "cyclic_imports"
    project = parse_project(root / "a.py", project_root=root)
    file_count = sum(
        1 for m in project.modules if m.file_path.endswith(("a.py", "b.py"))
    )
    assert file_count == 2


def test_determinism_three_runs_same_ids() -> None:
    """Re-parsing yields byte-identical module/edge IDs."""
    root = FIXTURES / "util_lib_pipeline"
    runs = [parse_project(root / "entry.py", project_root=root) for _ in range(3)]
    sigs = [
        tuple(sorted(m.id for m in p.modules))
        + tuple(sorted(
            (e.from_script_id, e.symbol, e.to_script_id or "")
            for e in p.import_edges
        ))
        for p in runs
    ]
    assert sigs[0] == sigs[1] == sigs[2]
