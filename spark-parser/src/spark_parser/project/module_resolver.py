"""Resolve Python `import` / `from … import …` statements to source-file paths.

The resolver is static (it does not execute or load any modules). It mirrors
CPython's lookup rules just well enough for cross-file lineage:

    absolute    `import pkg.sub.mod`            → <root>/pkg/sub/mod.py | …/__init__.py
    from-abs    `from pkg.sub import name`      → <root>/pkg/sub.py | …/__init__.py
    relative    `from .util import name`        → sibling of importing file
    relative+   `from ..pkg.sub import name`    → walk up parents

Third-party modules (``pyspark``, ``os``, anything outside the project root)
return ``None``. That is a normal outcome, not an error.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ResolvedImport:
    """One resolved import statement.

    ``file_path`` is the absolute path of the file that defines the imported
    name (the package ``__init__.py`` if a package was imported directly).
    ``is_package`` is True iff ``file_path`` ends with ``__init__.py``.
    """
    file_path: Path
    module_dotted: str
    is_package: bool


class ModuleResolver:
    """Static resolver scoped to a project root.

    Parameters
    ----------
    project_root:
        Directory that anchors absolute imports. Treated as ``sys.path[0]``.
    extra_search_paths:
        Additional roots to consult (e.g., a ``src/`` layout). Searched in
        order after ``project_root``.
    """

    def __init__(
        self,
        project_root: str | Path,
        *,
        extra_search_paths: list[str | Path] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.search_paths: list[Path] = [self.project_root]
        for p in extra_search_paths or []:
            self.search_paths.append(Path(p).resolve())

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def resolve_absolute(self, dotted: str) -> ResolvedImport | None:
        """Resolve ``pkg.sub.mod`` against the project's search paths."""
        return self._lookup_dotted(dotted)

    def resolve_relative(
        self,
        *,
        from_file: str | Path,
        level: int,
        module: str | None,
    ) -> ResolvedImport | None:
        """Resolve ``from .pkg import x`` (``level=1``) or ``from ..pkg`` (``level=2``).

        ``from_file`` is the absolute path of the importing source file.
        ``module`` is the dotted portion after the leading dots (may be ``None``
        for ``from . import x``).
        """
        if level < 1:
            return None
        anchor = Path(from_file).resolve().parent
        for _ in range(level - 1):
            anchor = anchor.parent
        if not self._is_within_project(anchor):
            return None

        parts: list[str] = []
        if module:
            parts.extend(module.split("."))

        return self._lookup_under(anchor, parts)

    # ------------------------------------------------------------------
    # Internal lookup helpers
    # ------------------------------------------------------------------

    def _lookup_dotted(self, dotted: str) -> ResolvedImport | None:
        if not dotted:
            return None
        parts = dotted.split(".")
        for root in self.search_paths:
            hit = self._lookup_under(root, parts)
            if hit is not None:
                return hit
        return None

    def _lookup_under(self, root: Path, parts: list[str]) -> ResolvedImport | None:
        if not parts:
            init = root / "__init__.py"
            if init.is_file():
                return ResolvedImport(
                    file_path=init.resolve(),
                    module_dotted="",
                    is_package=True,
                )
            return None

        *pkg_parts, last = parts
        directory = root.joinpath(*pkg_parts) if pkg_parts else root

        module_file = directory / f"{last}.py"
        if module_file.is_file():
            return ResolvedImport(
                file_path=module_file.resolve(),
                module_dotted=".".join(parts),
                is_package=False,
            )

        package_init = directory / last / "__init__.py"
        if package_init.is_file():
            return ResolvedImport(
                file_path=package_init.resolve(),
                module_dotted=".".join(parts),
                is_package=True,
            )

        return None

    def _is_within_project(self, p: Path) -> bool:
        """Reject relative imports that walk above any configured search root."""
        p = p.resolve()
        for root in self.search_paths:
            try:
                p.relative_to(root)
                return True
            except ValueError:
                continue
        return False
