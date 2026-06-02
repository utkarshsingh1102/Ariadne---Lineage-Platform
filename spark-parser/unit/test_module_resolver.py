"""Unit tests for ``spark_parser.project.module_resolver`` (v0.2 §1)."""
from __future__ import annotations

from pathlib import Path

import pytest

from spark_parser.project.module_resolver import ModuleResolver


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Build a sample project on disk:

        <root>/
          entry.py
          util.py
          pkg/__init__.py
          pkg/sub.py
          pkg/inner/__init__.py
          pkg/inner/leaf.py
    """
    (tmp_path / "entry.py").write_text("# entry\n")
    (tmp_path / "util.py").write_text("# util\n")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("# pkg/__init__\n")
    (pkg / "sub.py").write_text("# pkg/sub\n")
    inner = pkg / "inner"
    inner.mkdir()
    (inner / "__init__.py").write_text("# pkg/inner/__init__\n")
    (inner / "leaf.py").write_text("# pkg/inner/leaf\n")
    return tmp_path


def test_resolves_top_level_module(project_root: Path) -> None:
    r = ModuleResolver(project_root)
    hit = r.resolve_absolute("util")
    assert hit is not None
    assert hit.file_path == (project_root / "util.py").resolve()
    assert hit.is_package is False
    assert hit.module_dotted == "util"


def test_resolves_package_init(project_root: Path) -> None:
    r = ModuleResolver(project_root)
    hit = r.resolve_absolute("pkg")
    assert hit is not None
    assert hit.file_path == (project_root / "pkg" / "__init__.py").resolve()
    assert hit.is_package is True


def test_resolves_submodule(project_root: Path) -> None:
    r = ModuleResolver(project_root)
    hit = r.resolve_absolute("pkg.sub")
    assert hit is not None
    assert hit.file_path == (project_root / "pkg" / "sub.py").resolve()
    assert hit.is_package is False


def test_resolves_nested_package(project_root: Path) -> None:
    r = ModuleResolver(project_root)
    hit = r.resolve_absolute("pkg.inner.leaf")
    assert hit is not None
    assert hit.file_path == (project_root / "pkg" / "inner" / "leaf.py").resolve()


def test_third_party_returns_none(project_root: Path) -> None:
    r = ModuleResolver(project_root)
    assert r.resolve_absolute("pyspark.sql.functions") is None
    assert r.resolve_absolute("os") is None


def test_missing_module_returns_none(project_root: Path) -> None:
    r = ModuleResolver(project_root)
    assert r.resolve_absolute("does_not_exist") is None
    assert r.resolve_absolute("pkg.missing") is None


def test_relative_sibling(project_root: Path) -> None:
    r = ModuleResolver(project_root)
    # from .util import x  (level=1, module="util") at entry.py
    hit = r.resolve_relative(
        from_file=project_root / "entry.py", level=1, module="util",
    )
    assert hit is not None
    assert hit.file_path == (project_root / "util.py").resolve()


def test_relative_into_subpackage(project_root: Path) -> None:
    r = ModuleResolver(project_root)
    # from .inner.leaf import y at pkg/sub.py  (level=1 → anchor=pkg/)
    hit = r.resolve_relative(
        from_file=project_root / "pkg" / "sub.py",
        level=1,
        module="inner.leaf",
    )
    assert hit is not None
    assert hit.file_path == (project_root / "pkg" / "inner" / "leaf.py").resolve()


def test_relative_parent(project_root: Path) -> None:
    r = ModuleResolver(project_root)
    # from ..util import z at pkg/sub.py  (level=2 → anchor=<root>/)
    hit = r.resolve_relative(
        from_file=project_root / "pkg" / "sub.py",
        level=2,
        module="util",
    )
    assert hit is not None
    assert hit.file_path == (project_root / "util.py").resolve()


def test_relative_escapes_project_returns_none(project_root: Path) -> None:
    r = ModuleResolver(project_root)
    # from ...escaped import x at pkg/sub.py — level=3 walks above project root
    hit = r.resolve_relative(
        from_file=project_root / "pkg" / "sub.py",
        level=3,
        module="escaped",
    )
    assert hit is None


def test_relative_bare_dot(project_root: Path) -> None:
    """``from . import util`` at pkg/sub.py → anchor=pkg/, module=None.

    The resolver returns the anchor's ``__init__.py`` because the imported
    symbol is bound by the package, not a sub-module file.
    """
    r = ModuleResolver(project_root)
    hit = r.resolve_relative(
        from_file=project_root / "pkg" / "sub.py", level=1, module=None,
    )
    assert hit is not None
    assert hit.file_path == (project_root / "pkg" / "__init__.py").resolve()
    assert hit.is_package is True


def test_extra_search_paths(tmp_path: Path) -> None:
    src_root = tmp_path / "src"
    src_root.mkdir()
    (src_root / "lib.py").write_text("# lib\n")
    other = tmp_path / "other_root"
    other.mkdir()

    r = ModuleResolver(other, extra_search_paths=[src_root])
    hit = r.resolve_absolute("lib")
    assert hit is not None
    assert hit.file_path == (src_root / "lib.py").resolve()
