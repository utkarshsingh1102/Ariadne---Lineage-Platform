"""Live Spark UI REST client — v0.2 §11 (opt-in).

Spark History Server exposes ``/api/v1/applications/{appId}/stages`` and
``/sql`` endpoints that mirror the event log. This client is for environments
where the event log isn't archived but the History Server is reachable.

HTTP transport is injected for testability.
"""
from __future__ import annotations

from typing import Any, Callable

from ..models.domain import RuntimeIR, RuntimeJobIR, RuntimeStageIR, SqlExecutionIR


HttpClient = Callable[[str], tuple[int, list | dict | None]]


class SparkUIClient:
    def __init__(self, *, base_url: str, http: HttpClient) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http

    def fetch_runtime(self, app_id: str) -> RuntimeIR:
        out = RuntimeIR()

        # Stages
        status, stages = self.http(
            f"{self.base_url}/api/v1/applications/{app_id}/stages",
        )
        if status == 200 and isinstance(stages, list):
            for s in stages:
                out.stages.append(RuntimeStageIR(
                    stage_id=s.get("stageId", -1),
                    parent_ids=list(s.get("parentIds", [])),
                    name=s.get("name"),
                    num_tasks=s.get("numTasks"),
                    completed_ms=_iso_to_ms(s.get("completionTime")),
                ))

        # Jobs
        status, jobs = self.http(
            f"{self.base_url}/api/v1/applications/{app_id}/jobs",
        )
        if status == 200 and isinstance(jobs, list):
            for j in jobs:
                out.jobs.append(RuntimeJobIR(
                    job_id=j.get("jobId", -1),
                    stage_ids=list(j.get("stageIds", [])),
                    sql_execution_id=_int_or_none(j.get("sqlExecutionId")),
                    completed_ms=_iso_to_ms(j.get("completionTime")),
                ))

        # SQL executions
        status, sqls = self.http(
            f"{self.base_url}/api/v1/applications/{app_id}/sql",
        )
        if status == 200 and isinstance(sqls, list):
            for ex in sqls:
                out.sql_executions.append(SqlExecutionIR(
                    execution_id=ex.get("id", -1),
                    description=ex.get("description"),
                    physical_plan=ex.get("physicalPlan"),
                    completed_ms=_iso_to_ms(ex.get("completionTime")),
                    duration_ms=ex.get("duration"),
                ))

        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _iso_to_ms(v: Any) -> int | None:
    """Spark UI returns ISO-8601 strings; convert to epoch ms when possible."""
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return None
    return None
