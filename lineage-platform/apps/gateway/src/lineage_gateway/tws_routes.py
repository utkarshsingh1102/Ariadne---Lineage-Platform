"""/tws/* — Postgres-backed TWS operational view."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from . import postgres_client

router = APIRouter(prefix="/tws", tags=["tws"])


@router.get("/jobs")
async def jobs(
    start_time: str | None = Query(None, description="HH:MM lower bound (inclusive)"),
    end_time: str | None = Query(None, description="HH:MM upper bound (inclusive)"),
    script_path_like: str | None = Query(None),
    workstation: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    """Surfaces the v_runtime_window view from the TWS parser's Postgres mirror.

    The view is documented in tws-parser-plan.md §6.2 — it joins jobs to
    scripts and exposes a denormalized row per (job, planned-start, workstation).
    """
    pool = postgres_client.get_pool()
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail="Postgres pool not initialized — is the postgres service up?",
        )

    where: list[str] = []
    params: list[Any] = []

    if start_time:
        params.append(start_time)
        where.append(f"start_time >= ${len(params)}::time")
    if end_time:
        params.append(end_time)
        where.append(f"start_time <= ${len(params)}::time")
    if script_path_like:
        params.append(f"%{script_path_like}%")
        where.append(f"script_path ILIKE ${len(params)}")
    if workstation:
        params.append(workstation)
        where.append(f"workstation = ${len(params)}")

    clause = " WHERE " + " AND ".join(where) if where else ""
    params.append(limit)
    query = (
        f"SELECT job_name, workstation, start_time, end_time, script_path, "
        f"       schedule_name "
        f"FROM tws.v_runtime_window {clause} "
        f"ORDER BY start_time ASC NULLS LAST "
        f"LIMIT ${len(params)}"
    )

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"postgres error: {e}") from e

    return {"rows": [dict(r) for r in rows], "count": len(rows)}
