"""Project-scoped parser — recursively follows imports across files (v0.2 §1).

Two passes:

  Phase A — discover. Walk the entry script's imports DFS. For every reachable
  first-party file, collect its top-level ``FunctionDef`` nodes (cheap
  ``ast.parse`` only). Resolve each import edge to a concrete file path.

  Phase B — full parse. For each discovered file, build an
  ``external_functions`` table from its import edges + Phase A's per-file
  function tables, then call ``parse_pyspark`` with that table. The visitor
  uses it to inline-walk cross-module function calls.

Third-party imports (``pyspark``, ``os``, …) and unresolved paths are kept on
the edge with ``to_*=None`` so they remain visible in the import graph but do
not trigger recursion. Cycles are tolerated; each file is parsed at most once
per phase (visited set keyed by absolute path).
"""
from __future__ import annotations

import ast
from pathlib import Path

from ..models.domain import ImportEdgeIR, ProjectIR, SparkScriptIR, WarningIR
from ..pyspark.visitor import collect_top_level_functions, parse_pyspark
from ..utils.ids import script_id
from .module_resolver import ModuleResolver


class ProjectParser:
    """Walks an import DAG starting from an entry file."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        extra_search_paths: list[str | Path] | None = None,
        max_depth: int = 10,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.resolver = ModuleResolver(
            self.project_root, extra_search_paths=extra_search_paths,
        )
        self.max_depth = max_depth

    def parse(self, entry_path: str | Path) -> ProjectIR:
        entry = Path(entry_path).resolve()
        project = ProjectIR(
            entry_script_id=script_id(str(entry)),
            project_root=str(self.project_root),
        )

        # Phase A: discover all reachable first-party files + their FunctionDefs
        # and build the import-edge graph.
        discovered_fns: dict[Path, dict[str, ast.FunctionDef]] = {}
        raw_imports_by_file: dict[Path, list[dict]] = {}
        self._discover(
            entry, project, discovered_fns, raw_imports_by_file, depth=0,
        )

        # Phase B: full parse with the external-functions table per file.
        for path, raw_imports in raw_imports_by_file.items():
            external_fns = self._build_external_fn_table(
                from_file=path,
                raw_imports=raw_imports,
                discovered_fns=discovered_fns,
            )
            module_ir = parse_pyspark(path, external_functions=external_fns)
            # Patch ``to_script_id`` / ``to_file_path`` on the visitor-emitted
            # ImportEdgeIRs now that we've resolved them.
            for edge, raw in zip(module_ir.imports, raw_imports):
                target = self._resolve_raw_import(raw, from_file=path)
                if target is not None:
                    edge.to_file_path = str(target)
                    edge.to_script_id = script_id(str(target))
            project.modules.append(module_ir)
            project.import_edges.extend(module_ir.imports)

        return project

    # ------------------------------------------------------------------
    # Phase A — discovery
    # ------------------------------------------------------------------

    def _discover(
        self,
        path: Path,
        project: ProjectIR,
        fn_table: dict[Path, dict[str, ast.FunctionDef]],
        raw_imports_by_file: dict[Path, list[dict]],
        *,
        depth: int,
    ) -> None:
        path = path.resolve()
        if path in fn_table:
            return
        if depth > self.max_depth:
            project.warnings.append(WarningIR(
                type="import_depth_exceeded",
                detail=f"Skipping {path} — exceeds max_depth={self.max_depth}",
            ))
            return
        if not path.is_file():
            project.warnings.append(WarningIR(
                type="import_target_missing",
                detail=f"Resolved import target {path} does not exist",
            ))
            return

        fn_table[path] = collect_top_level_functions(path)
        raw_imports = self._extract_raw_imports(path)
        raw_imports_by_file[path] = raw_imports

        for raw in raw_imports:
            resolved = self._resolve_raw_import(raw, from_file=path)
            if resolved is None:
                continue
            self._discover(
                resolved, project, fn_table, raw_imports_by_file, depth=depth + 1,
            )

    def _extract_raw_imports(self, path: Path) -> list[dict]:
        """Pull Import / ImportFrom statements out of a file without running
        the full lineage visitor. Mirrors the visitor's encoding so Phase B
        sees the same edges as the single-file path.
        """
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            return []
        out: list[dict] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    out.append({
                        "kind": "import",
                        "symbol": local,
                        "module": alias.name,
                        "level": 0,
                        "original_symbol": alias.name,
                        "line": node.lineno,
                    })
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        # Star imports cannot be resolved to a specific symbol;
                        # the visitor still emits an edge so we keep parity.
                        out.append({
                            "kind": "from",
                            "symbol": "*",
                            "module": node.module,
                            "level": node.level or 0,
                            "original_symbol": "*",
                            "line": node.lineno,
                        })
                        continue
                    local = alias.asname or alias.name
                    out.append({
                        "kind": "from",
                        "symbol": local,
                        "module": node.module,
                        "level": node.level or 0,
                        "original_symbol": alias.name,
                        "line": node.lineno,
                    })
        return out

    def _resolve_raw_import(self, raw: dict, *, from_file: Path) -> Path | None:
        level = raw["level"]
        module = raw["module"]
        symbol = raw["original_symbol"]

        if level > 0:
            hit = self.resolver.resolve_relative(
                from_file=from_file, level=level, module=module,
            )
        elif module:
            hit = self.resolver.resolve_absolute(module)
        else:
            hit = None

        # `from <module> import X` may bind X to a submodule. Prefer that.
        if raw["kind"] == "from" and symbol:
            target_dotted = f"{module}.{symbol}" if module else symbol
            if level > 0:
                submodule = self.resolver.resolve_relative(
                    from_file=from_file, level=level, module=target_dotted,
                )
            else:
                submodule = self.resolver.resolve_absolute(target_dotted)
            if submodule is not None:
                return submodule.file_path

        return hit.file_path if hit is not None else None

    # ------------------------------------------------------------------
    # Phase B — build the external-functions table per file
    # ------------------------------------------------------------------

    def _build_external_fn_table(
        self,
        *,
        from_file: Path,
        raw_imports: list[dict],
        discovered_fns: dict[Path, dict[str, ast.FunctionDef]],
    ) -> dict[str, ast.FunctionDef]:
        table: dict[str, ast.FunctionDef] = {}
        for raw in raw_imports:
            resolved = self._resolve_raw_import(raw, from_file=from_file)
            if resolved is None or resolved not in discovered_fns:
                continue
            symbols = discovered_fns[resolved]
            # `from mod import fn` → bind local name to fn from mod.
            # `import mod` → cannot inline `mod.fn(...)` calls (Attribute access
            # is out of scope for v0.2 phase 1; we'd need to track the alias
            # and rewrite Attribute lookups). Skip for now.
            if raw["kind"] != "from":
                continue
            original = raw["original_symbol"]
            local = raw["symbol"]
            if original in symbols:
                table[local] = symbols[original]
        return table


def parse_project(
    entry_path: str | Path,
    *,
    project_root: str | Path,
    extra_search_paths: list[str | Path] | None = None,
) -> ProjectIR:
    """Convenience wrapper around ``ProjectParser.parse``."""
    return ProjectParser(
        project_root=project_root, extra_search_paths=extra_search_paths,
    ).parse(entry_path)
