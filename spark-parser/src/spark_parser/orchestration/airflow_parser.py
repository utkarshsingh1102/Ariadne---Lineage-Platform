"""Airflow DAG parser — v0.2 §7.

Walks a Python file that defines one or more Airflow DAGs and extracts:

  - The DAG id + ``schedule`` argument (if literal-string).
  - Every task: operator class name, ``task_id``, ``target_script`` for the
    common operators (``SparkSubmitOperator.application``,
    ``BashOperator.bash_command`` for ``spark-submit``, ``PythonOperator``
    callable name, ``DatabricksRunNowOperator``/``DatabricksSubmitRunOperator``
    notebook path or notebook task), and ``parameters`` (any literal kwargs).
  - Task dependencies inferred from ``a >> b``, ``b << a``,
    ``a.set_downstream(b)``, ``a.set_upstream(b)``, or
    ``chain(t1, t2, t3)``.

Static analysis only — Airflow is not imported and no DAG is executed.
"""
from __future__ import annotations

import ast
from pathlib import Path

from ..models.domain import (
    OrchestrationJobIR,
    OrchestrationTaskIR,
    TaskEdgeIR,
    WarningIR,
)


# Operator classes the parser knows how to extract a target-script for.
_OPERATORS_WITH_SCRIPT: dict[str, str] = {
    "SparkSubmitOperator": "application",
    "DatabricksRunNowOperator": "notebook_path",
    "DatabricksSubmitRunOperator": "notebook_path",
    "PythonOperator": "python_callable",
}
_BASH_OPERATOR = "BashOperator"


def parse_airflow_dag(file_path: str | Path) -> OrchestrationJobIR:
    p = Path(file_path)
    try:
        source = p.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(p))
    except (OSError, SyntaxError) as e:
        job = OrchestrationJobIR(job_id=p.stem, source="airflow", file_path=str(p))
        job.warnings.append(WarningIR(type="airflow_parse_error", detail=str(e)))
        return job

    walker = _AirflowWalker(p)
    walker.visit(tree)
    return walker.job


class _AirflowWalker(ast.NodeVisitor):
    """Light-weight Airflow AST walker.

    Tracks each ``ast.Name`` that's bound to a known operator instance so
    later expressions like ``task_a >> task_b`` resolve to two task_ids.
    """

    def __init__(self, path: Path):
        self.job = OrchestrationJobIR(
            job_id=path.stem, source="airflow", file_path=str(path),
        )
        # var name → task_id
        self._var_to_task: dict[str, str] = {}

    # --- DAG + task definitions ---------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        # DAG(dag_id=..., schedule=...)
        fn = node.func
        fn_name = _name_of(fn)
        if fn_name == "DAG":
            self._capture_dag(node)
        # ``chain(t1, t2, t3)`` — Airflow helper for linear task ordering.
        elif fn_name == "chain" and len(node.args) >= 2:
            self._emit_chain(node.args)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
        ):
            target_name = node.targets[0].id
            self._maybe_capture_operator(target_name, node.value)
        self.generic_visit(node)

    # --- Dependency syntax --------------------------------------------

    def visit_Expr(self, node: ast.Expr) -> None:
        # ``a >> b >> c`` parses as nested BinOp(RShift) expressions.
        if isinstance(node.value, ast.BinOp):
            self._maybe_capture_dependency(node.value)
        elif isinstance(node.value, ast.Call):
            fn_name = _name_of(node.value.func)
            # ``a.set_downstream(b)`` / ``a.set_upstream(b)``
            if isinstance(node.value.func, ast.Attribute):
                method = node.value.func.attr
                lhs = _name_of(node.value.func.value)
                if method == "set_downstream" and node.value.args:
                    self._record_dep(lhs, _name_of(node.value.args[0]))
                elif method == "set_upstream" and node.value.args:
                    self._record_dep(_name_of(node.value.args[0]), lhs)
        self.generic_visit(node)

    # --- Internals ----------------------------------------------------

    def _capture_dag(self, call: ast.Call) -> None:
        for kw in call.keywords:
            v = _const_or_none(kw.value)
            if kw.arg == "dag_id" and isinstance(v, str):
                self.job.job_id = v
            elif kw.arg == "schedule" and isinstance(v, str):
                self.job.schedule = v
            elif kw.arg in {"schedule_interval"} and isinstance(v, str):
                self.job.schedule = v

    def _maybe_capture_operator(self, var: str, call: ast.Call) -> None:
        op_name = _name_of(call.func)
        if op_name is None:
            return

        task_id: str | None = None
        params: dict[str, str] = {}
        target: str | None = None

        for kw in call.keywords:
            v = _const_or_none(kw.value)
            if kw.arg == "task_id" and isinstance(v, str):
                task_id = v
            elif isinstance(v, (str, int, float, bool)):
                params[kw.arg or ""] = str(v)

        if op_name in _OPERATORS_WITH_SCRIPT:
            target = params.get(_OPERATORS_WITH_SCRIPT[op_name])
        elif op_name == _BASH_OPERATOR:
            cmd = params.get("bash_command", "")
            if "spark-submit" in cmd:
                # Pull out the last token that looks like a .py / .jar path.
                for tok in cmd.split():
                    if tok.endswith((".py", ".jar")):
                        target = tok
                        break

        if op_name not in _OPERATORS_WITH_SCRIPT and op_name != _BASH_OPERATOR:
            # Not an operator we recognise — skip, don't pollute the task list.
            return

        if task_id is None:
            task_id = var  # fall back to the variable name
        self._var_to_task[var] = task_id
        self.job.tasks.append(OrchestrationTaskIR(
            task_id=task_id, operator=op_name,
            target_script=target, parameters=params,
            line=call.lineno,
        ))

    def _maybe_capture_dependency(self, node: ast.BinOp) -> None:
        """Recognise ``a >> b`` / ``a << b`` chains, including ``a >> b >> c``.

        Python parses ``a >> b >> c`` as ``BinOp(BinOp(a, b), c)`` (left
        associative). Flatten the whole expression first, then emit adjacent
        pairs so ``(a → b)`` and ``(b → c)`` both land.
        """
        if isinstance(node.op, ast.RShift):
            names = self._flatten_chain(node, ast.RShift)
            for u, d in zip(names, names[1:]):
                self._record_dep(u, d)
        elif isinstance(node.op, ast.LShift):
            names = self._flatten_chain(node, ast.LShift)
            for u, d in zip(names, names[1:]):
                # `a << b << c` reads "a is downstream of b which is downstream of c"
                self._record_dep(d, u)

    def _flatten_chain(self, node: ast.AST, op: type) -> list[str]:
        """For ``a >> b >> c`` returns [a, b, c] when called on the outer BinOp."""
        if isinstance(node, ast.BinOp) and isinstance(node.op, op):
            return self._flatten_chain(node.left, op) + self._flatten_chain(node.right, op)
        name = _name_of(node)
        return [name] if name else []

    def _emit_chain(self, args: list[ast.AST]) -> None:
        names = [_name_of(a) for a in args if _name_of(a)]
        for u, d in zip(names, names[1:]):
            self._record_dep(u, d)

    def _record_dep(self, lhs_var: str | None, rhs_var: str | None) -> None:
        if not lhs_var or not rhs_var:
            return
        upstream = self._var_to_task.get(lhs_var, lhs_var)
        downstream = self._var_to_task.get(rhs_var, rhs_var)
        # De-duplicate
        for e in self.job.edges:
            if e.upstream == upstream and e.downstream == downstream:
                return
        self.job.edges.append(TaskEdgeIR(upstream=upstream, downstream=downstream))


def _name_of(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _const_or_none(node: ast.AST | None):
    if isinstance(node, ast.Constant):
        return node.value
    return None
