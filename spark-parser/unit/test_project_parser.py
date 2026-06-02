"""Unit tests for ``ProjectParser`` — v0.2 §1 cross-file resolution."""
from __future__ import annotations

from pathlib import Path

import pytest

from spark_parser.project.project_parser import ProjectParser, parse_project

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "projects"


def test_util_lib_pipeline_discovers_both_modules() -> None:
    root = FIXTURES / "util_lib_pipeline"
    project = parse_project(root / "entry.py", project_root=root)

    paths = sorted(m.file_path for m in project.modules)
    assert any(p.endswith("entry.py") for p in paths)
    assert any(p.endswith("util.py") for p in paths)
    assert len(project.modules) == 2


def test_util_lib_pipeline_records_import_edge() -> None:
    root = FIXTURES / "util_lib_pipeline"
    project = parse_project(root / "entry.py", project_root=root)

    entry_mod = next(m for m in project.modules if m.file_path.endswith("entry.py"))
    util_mod = next(m for m in project.modules if m.file_path.endswith("util.py"))

    edges_from_entry = [e for e in project.import_edges if e.from_script_id == entry_mod.id]
    enrich_edge = next(e for e in edges_from_entry if e.symbol == "enrich")
    assert enrich_edge.kind == "from"
    assert enrich_edge.to_script_id == util_mod.id
    assert enrich_edge.to_file_path == util_mod.file_path


def test_relative_imports_resolve() -> None:
    root = FIXTURES / "relative_imports"
    project = parse_project(root / "pkg" / "main.py", project_root=root)

    paths = sorted(m.file_path for m in project.modules)
    assert any(p.endswith("pkg/main.py") for p in paths)
    assert any(p.endswith("pkg/transforms.py") for p in paths)


def test_package_dag_two_hops() -> None:
    root = FIXTURES / "package_dag"
    project = parse_project(root / "pipeline" / "main.py", project_root=root)

    # main → transforms → utils — three modules reachable
    names = {Path(m.file_path).name for m in project.modules}
    assert names == {"main.py", "transforms.py", "utils.py"}


def test_cyclic_imports_terminate() -> None:
    root = FIXTURES / "cyclic_imports"
    project = parse_project(root / "a.py", project_root=root)

    # Both files appear once; no duplicate parse, no infinite recursion.
    names = sorted(Path(m.file_path).name for m in project.modules)
    assert names == ["a.py", "b.py"]


def test_third_party_imports_have_no_target(tmp_path: Path) -> None:
    """`import os` and `from pyspark.sql import SparkSession` resolve to None."""
    (tmp_path / "x.py").write_text(
        "import os\n"
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
    )
    project = parse_project(tmp_path / "x.py", project_root=tmp_path)

    third_party = [e for e in project.import_edges if e.to_script_id is None]
    # `os` + `SparkSession` — two third-party edges
    assert len(third_party) >= 2
    symbols = {e.symbol for e in third_party}
    assert "os" in symbols
    assert "SparkSession" in symbols


def test_max_depth_caps_recursion(tmp_path: Path) -> None:
    """Deep linear import chain truncates at max_depth."""
    # Chain: m0 → m1 → m2 → m3 → m4
    for i in range(5):
        contents = (
            "" if i == 4
            else f"from m{i + 1} import x{i + 1}\n"
        )
        (tmp_path / f"m{i}.py").write_text(contents + f"x{i} = 1\n")

    project = ProjectParser(project_root=tmp_path, max_depth=2).parse(tmp_path / "m0.py")
    # Depth 0=m0, 1=m1, 2=m2 → m3 is rejected by max_depth check
    names = {Path(m.file_path).name for m in project.modules}
    assert "m0.py" in names
    assert "m1.py" in names
    assert "m2.py" in names
    assert any(w.type == "import_depth_exceeded" for w in project.warnings)
