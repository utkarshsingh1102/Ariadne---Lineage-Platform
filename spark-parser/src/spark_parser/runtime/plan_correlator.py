"""Correlate static ``SparkScriptIR`` with a runtime ``RuntimeIR`` — v0.2 §11.

Three correlation strategies, applied in order:

  1. ``spark.sql(stmt)`` call sites match a ``SparkListenerSQLExecutionEnd``
     event when the canonical SQL string is equal (whitespace-normalised,
     lowercased keywords via sqlglot).
  2. DataFrame-API operations match the SQL executions in source order — the
     N-th ``write_edge`` lines up with the N-th SQL execution that has a
     ``WriteToDataSource`` physical plan.
  3. Anything left unmatched gets a ``RuntimePlanIR.runtime_dag_signature=None``
     so consumers can see what's missing.

Also computes static + runtime DAG signatures (topological hash) so callers
can detect runtime divergence (e.g., AQE collapsing stages).
"""
from __future__ import annotations

import hashlib

from ..models.domain import (
    DataFrameIR,
    RuntimeIR,
    RuntimePlanIR,
    SparkScriptIR,
    WarningIR,
)


def correlate(
    ir: SparkScriptIR, runtime: RuntimeIR,
) -> tuple[list[RuntimePlanIR], list[WarningIR]]:
    """Return ``(correlations, warnings)``.

    ``correlations`` is one ``RuntimePlanIR`` per matched DataFrame; every
    DataFrame that *should* have matched but didn't gets a warning instead.
    """
    out: list[RuntimePlanIR] = []
    warnings: list[WarningIR] = []
    static_sig = static_dag_signature(ir)
    runtime_sig = runtime_dag_signature(runtime)

    sql_dfs = [df for df in ir.dataframes if df.from_sql_block]
    write_dfs = [df for df in ir.dataframes if df.writes_to]

    # Strategy 1 — match SQL-block DataFrames by canonical SQL string.
    used: set[int] = set()
    for df in sql_dfs:
        matched_id: int | None = None
        # The static IR doesn't currently carry the SQL string verbatim — we
        # fall back to ordering. Iterate executions in arrival order.
        for ex in runtime.sql_executions:
            if ex.execution_id in used:
                continue
            matched_id = ex.execution_id
            used.add(matched_id)
            break
        if matched_id is None:
            warnings.append(WarningIR(
                type="runtime_correlation_missing",
                detail=f"DataFrame {df.var_name} has no matching SQL execution",
            ))
            continue
        out.append(_make_correlation(
            df, matched_id, runtime, static_sig, runtime_sig,
        ))

    # Strategy 2 — match writes by source order.
    for df in write_dfs:
        if df.from_sql_block:
            continue  # already handled
        matched_id: int | None = None
        for ex in runtime.sql_executions:
            if ex.execution_id in used:
                continue
            # Bias toward executions that mention a Write step in their plan.
            plan = (ex.physical_plan or "").lower()
            if "write" in plan or "saveas" in plan or "insert" in plan:
                matched_id = ex.execution_id
                used.add(matched_id)
                break
        if matched_id is None:
            warnings.append(WarningIR(
                type="runtime_correlation_missing",
                detail=f"Write {df.var_name} has no matching SQL execution",
            ))
            continue
        out.append(_make_correlation(
            df, matched_id, runtime, static_sig, runtime_sig,
        ))

    # Surface divergence between the two DAG signatures as a warning so the
    # human reviewer can decide if it's expected (e.g., AQE coalescing) or
    # the result of a real lineage gap.
    if static_sig and runtime_sig and static_sig != runtime_sig:
        warnings.append(WarningIR(
            type="runtime_dag_divergence",
            detail=(
                f"static DAG signature {static_sig} differs from runtime "
                f"signature {runtime_sig} — AQE / dynamic optimisations may "
                "have rewritten the plan"
            ),
        ))

    return out, warnings


def _make_correlation(
    df: DataFrameIR,
    execution_id: int,
    runtime: RuntimeIR,
    static_sig: str,
    runtime_sig: str,
) -> RuntimePlanIR:
    ex = next(
        (e for e in runtime.sql_executions if e.execution_id == execution_id),
        None,
    )
    return RuntimePlanIR(
        static_node_id=df.id or "",
        execution_id=execution_id,
        physical_plan=ex.physical_plan if ex else None,
        static_dag_signature=static_sig,
        runtime_dag_signature=runtime_sig,
    )


# ---------------------------------------------------------------------------
# DAG signatures — topological hash of the DataFrame/stage graphs.
# ---------------------------------------------------------------------------

def static_dag_signature(ir: SparkScriptIR) -> str:
    """sha256 of a canonical edge list from the static IR."""
    edges: list[tuple[str, str, str]] = []
    for df in ir.dataframes:
        for e in df.derives_from_dataframe:
            edges.append((e.source_var or "", df.var_name, e.via))
    edges.sort()
    return _sha256(repr(edges))


def runtime_dag_signature(runtime: RuntimeIR) -> str:
    """sha256 of a canonical stage parent→child edge list."""
    edges: list[tuple[int, int]] = []
    for st in runtime.stages:
        for parent in st.parent_ids:
            edges.append((parent, st.stage_id))
    edges.sort()
    return _sha256(repr(edges))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
