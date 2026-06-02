"""Databricks workflow JSON parser — v0.2 §7.

Reads a workflow definition file (the JSON shape returned by the Databricks
Jobs API 2.1) and emits one ``OrchestrationJobIR`` per top-level job, with
each task and its dependencies preserved.

Recognised task types: ``notebook_task``, ``spark_python_task``,
``spark_jar_task``, ``python_wheel_task``, ``dbt_task``. Each is mapped to a
``target_script`` (the notebook path, ``.py`` file, JAR coordinates, etc.) so
downstream tooling can stitch the workflow into the Spark lineage graph.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..models.domain import (
    OrchestrationJobIR,
    OrchestrationTaskIR,
    TaskEdgeIR,
    WarningIR,
)


_TASK_TYPE_TO_TARGET_FIELDS: list[tuple[str, str, str]] = [
    # (task_key_in_json, sub_key_to_use_as_target, operator_label)
    ("notebook_task",       "notebook_path",        "notebook"),
    ("spark_python_task",   "python_file",          "spark_python"),
    ("python_wheel_task",   "package_name",         "python_wheel"),
    ("spark_jar_task",      "main_class_name",      "spark_jar"),
    ("dbt_task",            "schema",               "dbt"),
    ("sql_task",            "warehouse_id",         "sql"),
    ("pipeline_task",       "pipeline_id",          "pipeline"),
]


def parse_databricks_workflow(file_path: str | Path) -> OrchestrationJobIR:
    p = Path(file_path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        job = OrchestrationJobIR(
            job_id=p.stem, source="databricks_workflow", file_path=str(p),
        )
        job.warnings.append(WarningIR(
            type="workflow_parse_error", detail=str(e),
        ))
        return job

    return _job_from_dict(data, file_path=str(p))


def _job_from_dict(data: dict, *, file_path: str) -> OrchestrationJobIR:
    name = data.get("name") or data.get("settings", {}).get("name") or Path(file_path).stem
    settings = data.get("settings", data)         # both shapes are common
    job = OrchestrationJobIR(
        job_id=name, source="databricks_workflow", file_path=file_path,
        schedule=_extract_schedule(settings),
    )

    tasks = settings.get("tasks") or []
    for raw in tasks:
        task_key = raw.get("task_key") or raw.get("key") or ""
        if not task_key:
            continue
        target, operator, params = _extract_target(raw)
        job.tasks.append(OrchestrationTaskIR(
            task_id=task_key,
            operator=operator,
            target_script=target,
            parameters=params,
        ))
        for dep in raw.get("depends_on") or []:
            up = dep.get("task_key") if isinstance(dep, dict) else None
            if up:
                job.edges.append(TaskEdgeIR(upstream=up, downstream=task_key))
    return job


def _extract_schedule(settings: dict) -> str | None:
    sched = settings.get("schedule")
    if isinstance(sched, dict):
        return sched.get("quartz_cron_expression") or sched.get("timezone_id")
    if isinstance(sched, str):
        return sched
    trigger = settings.get("trigger") or {}
    cron = trigger.get("cron") if isinstance(trigger, dict) else None
    if isinstance(cron, dict):
        return cron.get("quartz_cron_expression")
    return None


def _extract_target(task: dict) -> tuple[str | None, str, dict[str, str]]:
    """For a single task dict, return (target_script, operator_label, params)."""
    for key, sub_key, operator in _TASK_TYPE_TO_TARGET_FIELDS:
        body = task.get(key)
        if not isinstance(body, dict):
            continue
        target = body.get(sub_key)
        params: dict[str, str] = {}
        for k, v in body.items():
            if isinstance(v, (str, int, float, bool)) and k != sub_key:
                params[k] = str(v)
        if isinstance(target, str):
            return target, operator, params
        return None, operator, params
    return None, "unknown", {}
