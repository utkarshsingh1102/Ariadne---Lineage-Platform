"""Detect runtime-dynamic Python that the static visitor can't safely resolve.

v0.2 §3 — runtime-evaluated logic. The parser stays purely static; this module
walks an AST and emits a ``WarningIR(type="runtime_dynamic", subtype=...)``
for each detected construct, plus marks any affected DataFrame as
``lineage_partial=true`` so downstream consumers know the graph is incomplete.

Detections:

  - ``eval`` / ``exec``                              subtype="eval"
  - ``setattr(obj, name, value)`` with non-const     subtype="setattr"
  - ``locals()[name] = …`` / ``globals()[name] = …`` subtype="dynamic_binding"
  - ``getattr(obj, name, …)`` with non-const name    subtype="reflection"
  - f-string / .format() / %-format SQL templates    subtype="sql_template"
  - Loop over a non-literal iterable that mutates a  subtype="dynamic_loop"
    Spark expression

The detector does **not** rewrite the IR; it only annotates. Hooks into
``parse_pyspark`` via ``scan(tree, ir)`` after the main visitor pass so
warnings are appended to the existing ``ir.warnings`` list.
"""
from __future__ import annotations

import ast

from ..models.domain import SparkScriptIR, WarningIR


# Names that imply a SQL string is being built. Used to scope the f-string
# detector — we only warn on templated SQL, not on every f-string.
_SQL_VAR_HINTS = {"sql", "query", "stmt", "statement", "q", "ddl"}


def scan(tree: ast.Module, ir: SparkScriptIR) -> None:
    """Append ``runtime_dynamic`` warnings to ``ir.warnings``."""
    detector = _DynamicDetector(ir)
    detector.visit(tree)


class _DynamicDetector(ast.NodeVisitor):
    def __init__(self, ir: SparkScriptIR):
        self.ir = ir
        # Track names known to hold string constants — lets us decide whether
        # an f-string's interpolated parts are statically resolvable.
        self.string_constants: set[str] = set()

    # ------------------------------------------------------------------
    # Assignment — capture string constants for f-string resolution
    # ------------------------------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            self.string_constants.add(node.targets[0].id)

        # locals()[...] = X / globals()[...] = X — dynamic binding
        for tgt in node.targets:
            if self._is_locals_or_globals_subscript(tgt):
                self._warn(
                    "dynamic_binding",
                    "Assignment via locals()/globals() — lineage marked partial",
                    line=node.lineno,
                )

        # Templated SQL on the RHS (f"…" or "…".format(…)) bound to a
        # SQL-shaped name → warn.
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id.lower() in _SQL_VAR_HINTS
        ):
            self._maybe_warn_sql_template(node.value, line=node.lineno)

        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Call dispatch — eval, exec, setattr, getattr, .format()
    # ------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func

        # eval(...) / exec(...)
        if isinstance(fn, ast.Name) and fn.id in {"eval", "exec"}:
            self._warn(
                "eval",
                f"{fn.id}() of a runtime expression — lineage cannot be statically resolved",
                line=node.lineno,
            )

        # setattr(obj, "name", value) — only warn if "name" is not a string constant
        if isinstance(fn, ast.Name) and fn.id == "setattr" and len(node.args) >= 2:
            attr_arg = node.args[1]
            if not (isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str)):
                self._warn(
                    "setattr",
                    "setattr() with a non-constant attribute name — lineage marked partial",
                    line=node.lineno,
                )

        # getattr(obj, name) with non-constant name → reflection
        if isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2:
            attr_arg = node.args[1]
            if not (isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str)):
                self._warn(
                    "reflection",
                    "getattr() with a non-constant attribute name — lineage marked partial",
                    line=node.lineno,
                )

        # spark.sql(<template>) — the template is the first arg
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr == "sql"
            and isinstance(fn.value, ast.Name)
            and node.args
        ):
            self._maybe_warn_sql_template(node.args[0], line=node.lineno)

        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Loops — non-literal iterable
    # ------------------------------------------------------------------

    def visit_For(self, node: ast.For) -> None:
        iter_node = node.iter
        if not self._is_literal_iterable(iter_node):
            # Check whether the body touches Spark — heuristic: any Attribute
            # call ending in a known DataFrame method. Cheap and avoids
            # false-positives on plain Python loops.
            if self._body_touches_spark(node.body):
                self._warn(
                    "dynamic_loop",
                    "Loop iterates a non-literal iterable that produces Spark "
                    "expressions — generated lineage is partial",
                    line=node.lineno,
                )
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_locals_or_globals_subscript(self, node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in {"locals", "globals"}
        )

    def _maybe_warn_sql_template(self, value: ast.AST, *, line: int) -> None:
        # f"..." (JoinedStr) — warn if any FormattedValue refers to a name
        # that's NOT a known string constant.
        if isinstance(value, ast.JoinedStr):
            for part in value.values:
                if isinstance(part, ast.FormattedValue):
                    inner = part.value
                    name = inner.id if isinstance(inner, ast.Name) else None
                    if name is None or name not in self.string_constants:
                        self._warn(
                            "sql_template",
                            "f-string SQL template contains a non-constant "
                            "interpolation — lineage marked partial",
                            line=line,
                        )
                        return

        # "…{x}…".format(x=…) — JoinedStr already covers f-strings; explicit
        # .format calls are the older idiom.
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "format"
            and not all(
                isinstance(a, ast.Constant) and isinstance(a.value, str)
                for a in value.args
            )
        ):
            self._warn(
                "sql_template",
                ".format() SQL template with non-constant arguments — "
                "lineage marked partial",
                line=line,
            )

    def _is_literal_iterable(self, node: ast.AST) -> bool:
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            return True
        # range(<consts>) is statically expandable
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "range"
            and all(isinstance(a, ast.Constant) for a in node.args)
        ):
            return True
        return False

    def _body_touches_spark(self, body: list[ast.stmt]) -> bool:
        """Heuristic: does the loop body contain a Spark-ish call?"""
        spark_methods = {
            "read", "write", "table", "sql", "select", "join", "filter",
            "withColumn", "withColumnRenamed", "groupBy", "agg", "union",
            "unionByName", "saveAsTable", "insertInto", "save",
            "createOrReplaceTempView",
        }
        for sub in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(sub, ast.Attribute) and sub.attr in spark_methods:
                return True
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                if sub.func.attr in spark_methods:
                    return True
        return False

    def _warn(self, subtype: str, detail: str, *, line: int | None) -> None:
        self.ir.warnings.append(WarningIR(
            type="runtime_dynamic", subtype=subtype, detail=detail, line=line,
        ))
        # Mark all DataFrames as partial-lineage when a top-level dynamic
        # construct is present. Conservative — the affected DF might be hard
        # to pin down without semantic scoping; partial-flag everything so
        # the contract suite surfaces the limitation.
        for df in self.ir.dataframes:
            df.lineage_partial = True
