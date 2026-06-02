"""Spark event-log reader — v0.2 §11.

Reads a directory (or single file) of Spark event-log JSON-Lines and emits a
``RuntimeIR`` containing every SQL execution, job, stage, and Catalyst
optimization decision it can recover.

Spark writes one JSON object per line; we tolerate empty lines and lines that
fail to parse (those are appended as warnings). The reader is event-driven
and does not require a running cluster.

Supported event types:

  - ``org.apache.spark.sql.execution.ui.SparkListenerSQLExecutionStart`` /
    ``…SparkListenerSQLExecutionEnd`` — collect ``executionId``,
    ``description``, ``physicalPlanDescription``, plus the analyzed /
    optimized plan text when Spark wrote them.
  - ``SparkListenerJobStart`` / ``SparkListenerJobEnd`` — job → stage map.
  - ``SparkListenerStageSubmitted`` / ``SparkListenerStageCompleted`` —
    stage shape (parent ids) and completion time.

Catalyst rule applications surface in ``executionEnd`` events as a list of
``rule`` strings under ``sparkPlanInfo`` — we extract them as
``OptimizationDecisionIR`` records.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from ..models.domain import (
    OptimizationDecisionIR,
    RuntimeIR,
    RuntimeJobIR,
    RuntimeStageIR,
    SqlExecutionIR,
    WarningIR,
)


def read_event_log(path: str | Path) -> RuntimeIR:
    """Parse one event log file *or* directory of files into a RuntimeIR."""
    p = Path(path)
    out = RuntimeIR()
    if not p.exists():
        out.warnings.append(WarningIR(
            type="event_log_missing", detail=f"path not found: {p}",
        ))
        return out

    files: list[Path]
    if p.is_dir():
        files = sorted(p.iterdir())
    else:
        files = [p]

    # Accumulate state across files (Spark sometimes writes rolling logs).
    sql_starts: dict[int, dict] = {}
    job_stage_ids: dict[int, list[int]] = {}
    job_sql_execution: dict[int, int | None] = {}
    stage_parents: dict[int, list[int]] = {}
    stage_names: dict[int, str | None] = {}
    stage_num_tasks: dict[int, int | None] = {}
    job_completion_ms: dict[int, int] = {}
    stage_completion_ms: dict[int, int] = {}

    def _emit_warning(detail: str) -> None:
        out.warnings.append(WarningIR(
            type="event_log_parse_error", detail=detail,
        ))

    for f in files:
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as e:
            _emit_warning(f"{f.name}: {e}")
            continue
        for lineno, raw in enumerate(text.splitlines(), 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError as e:
                _emit_warning(f"{f.name}:{lineno}: {e}")
                continue
            _consume(
                ev, out,
                sql_starts, job_stage_ids, job_sql_execution,
                stage_parents, stage_names, stage_num_tasks,
                job_completion_ms, stage_completion_ms,
            )

    # Flush deferred shapes — jobs and stages we saw "submitted" but never
    # "completed" still get an entry, just without a completion time.
    for job_id, stage_ids in job_stage_ids.items():
        out.jobs.append(RuntimeJobIR(
            job_id=job_id,
            stage_ids=stage_ids,
            sql_execution_id=job_sql_execution.get(job_id),
            completed_ms=job_completion_ms.get(job_id),
        ))
    for stage_id, parents in stage_parents.items():
        out.stages.append(RuntimeStageIR(
            stage_id=stage_id,
            parent_ids=parents,
            name=stage_names.get(stage_id),
            num_tasks=stage_num_tasks.get(stage_id),
            completed_ms=stage_completion_ms.get(stage_id),
        ))

    return out


# ---------------------------------------------------------------------------
# Per-event dispatch
# ---------------------------------------------------------------------------

_SQL_START = "org.apache.spark.sql.execution.ui.SparkListenerSQLExecutionStart"
_SQL_END = "org.apache.spark.sql.execution.ui.SparkListenerSQLExecutionEnd"
_JOB_START = "SparkListenerJobStart"
_JOB_END = "SparkListenerJobEnd"
_STAGE_SUBMITTED = "SparkListenerStageSubmitted"
_STAGE_COMPLETED = "SparkListenerStageCompleted"


def _consume(
    ev: dict,
    out: RuntimeIR,
    sql_starts: dict[int, dict],
    job_stage_ids: dict[int, list[int]],
    job_sql_execution: dict[int, int | None],
    stage_parents: dict[int, list[int]],
    stage_names: dict[int, str | None],
    stage_num_tasks: dict[int, int | None],
    job_completion_ms: dict[int, int],
    stage_completion_ms: dict[int, int],
) -> None:
    event_name = ev.get("Event") or ev.get("event")
    if event_name == _SQL_START:
        eid = ev.get("executionId")
        if isinstance(eid, int):
            sql_starts[eid] = ev
    elif event_name == _SQL_END:
        eid = ev.get("executionId")
        if not isinstance(eid, int):
            return
        start = sql_starts.pop(eid, {})
        plan_info = ev.get("sparkPlanInfo") or start.get("sparkPlanInfo") or {}
        # Collect any Catalyst rule strings the event reports.
        for rule_obj in ev.get("rules") or []:
            rule_name = rule_obj.get("ruleName") if isinstance(rule_obj, dict) else None
            if rule_name:
                out.optimizations.append(OptimizationDecisionIR(
                    execution_id=eid,
                    rule=rule_name,
                    detail=str(rule_obj.get("detail") or "") or None,
                ))
        out.sql_executions.append(SqlExecutionIR(
            execution_id=eid,
            description=start.get("description"),
            physical_plan=(
                ev.get("physicalPlanDescription")
                or start.get("physicalPlanDescription")
                or _sparkplan_to_string(plan_info)
            ),
            analyzed_plan=ev.get("analyzedPlan") or start.get("analyzedPlan"),
            optimized_plan=ev.get("optimizedPlan") or start.get("optimizedPlan"),
            duration_ms=ev.get("duration"),
            completed_ms=ev.get("time"),
        ))
    elif event_name == _JOB_START:
        jid = ev["Job ID"] if "Job ID" in ev else ev.get("jobId")
        if not isinstance(jid, int):
            return
        stage_infos = ev.get("Stage Infos") or ev.get("stageInfos") or []
        stage_ids = []
        for s in stage_infos:
            sid = s.get("Stage ID") if isinstance(s, dict) else None
            if isinstance(sid, int):
                stage_ids.append(sid)
                stage_names[sid] = s.get("Stage Name")
                stage_num_tasks[sid] = s.get("Number of Tasks")
                parents = s.get("Parent IDs") or s.get("parentIds") or []
                stage_parents[sid] = [p for p in parents if isinstance(p, int)]
        job_stage_ids[jid] = stage_ids
        props = ev.get("Properties") or {}
        exec_id_raw = props.get("spark.sql.execution.id")
        try:
            job_sql_execution[jid] = int(exec_id_raw) if exec_id_raw is not None else None
        except (TypeError, ValueError):
            job_sql_execution[jid] = None
    elif event_name == _JOB_END:
        jid = ev["Job ID"] if "Job ID" in ev else ev.get("jobId")
        if isinstance(jid, int):
            completion = ev.get("Completion Time") or ev.get("completionTime")
            if isinstance(completion, int):
                job_completion_ms[jid] = completion
    elif event_name == _STAGE_SUBMITTED:
        info = ev.get("Stage Info") or ev.get("stageInfo") or {}
        sid = info.get("Stage ID")
        if isinstance(sid, int):
            stage_parents.setdefault(sid, [
                p for p in info.get("Parent IDs", []) if isinstance(p, int)
            ])
            stage_names.setdefault(sid, info.get("Stage Name"))
            stage_num_tasks.setdefault(sid, info.get("Number of Tasks"))
    elif event_name == _STAGE_COMPLETED:
        info = ev.get("Stage Info") or ev.get("stageInfo") or {}
        sid = info.get("Stage ID")
        completion = info.get("Completion Time") or info.get("completionTime")
        if isinstance(sid, int) and isinstance(completion, int):
            stage_completion_ms[sid] = completion


def _sparkplan_to_string(info: Any) -> str | None:
    """Render a SparkPlanInfo dict into a single-line description.

    Spark serialises plans as nested dicts with ``nodeName`` + ``children``;
    we walk pre-order and join names so consumers can match on plan shape.
    """
    if not isinstance(info, dict):
        return None
    out: list[str] = []
    stack: list[dict] = [info]
    while stack:
        node = stack.pop(0)
        name = node.get("nodeName")
        if isinstance(name, str):
            out.append(name)
        children = node.get("children") or []
        for c in children:
            if isinstance(c, dict):
                stack.append(c)
    return " -> ".join(out) if out else None
