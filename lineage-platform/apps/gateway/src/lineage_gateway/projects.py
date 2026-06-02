"""Projects — user-named groupings of parsed files across all source types.

A project lets a user upload N files (any mix of tableau/tws/qlikview/spark)
under one umbrella name; the Files page can then show those files
sub-grouped by parser type, and the lineage explorer can scope traversal to
the project (or a subset of its files).

Schema lives in Postgres. ``init.sql`` declares the tables on a fresh
volume; ``ensure_schema()`` re-runs the same CREATE IF NOT EXISTS on every
gateway boot so existing deployments pick the tables up without a manual
migration.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .postgres_client import get_pool

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Schema bootstrap (idempotent — safe to call repeatedly)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id          UUID PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL CHECK (length(trim(name)) > 0),
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS project_files (
    project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    neo4j_id     TEXT NOT NULL,
    source_type  TEXT NOT NULL CHECK (source_type IN ('tableau', 'tws', 'qlikview', 'spark')),
    file_name    TEXT,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, neo4j_id)
);
CREATE INDEX IF NOT EXISTS project_files_project_idx ON project_files (project_id);
CREATE INDEX IF NOT EXISTS project_files_source_idx ON project_files (source_type);
"""


async def ensure_schema() -> None:
    """Run the CREATE-IF-NOT-EXISTS bootstrap on the active pool. No-op if
    Postgres is unreachable (the gateway can still serve other routes)."""
    pool = get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA_SQL)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None


class ProjectFileRef(BaseModel):
    neo4j_id: str
    source_type: str
    file_name: str | None = None


class ProjectSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    created_at: str
    file_count: int
    by_source: dict[str, int]


class ProjectFile(BaseModel):
    neo4j_id: str
    source_type: str
    file_name: str | None = None
    added_at: str


class ProjectDetail(BaseModel):
    id: str
    name: str
    description: str | None = None
    created_at: str
    files: list[ProjectFile]
    by_source: dict[str, list[ProjectFile]]


# ---------------------------------------------------------------------------
# Internal helpers — used by the /parse/upload/auto handler to record
# parsed files against a project in one transaction.
# ---------------------------------------------------------------------------


async def get_or_create_project(
    pool: asyncpg.Pool, name: str, description: str | None = None,
) -> str:
    """Look up a project by name; create it (with a new UUID) if not found.
    Returns the project id as a string. Trimmed names are compared.

    Raises HTTPException(409) if the name exists but the lookup race-conditions.
    """
    normalised = name.strip()
    if not normalised:
        raise HTTPException(status_code=400, detail="project name cannot be empty")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM projects WHERE name = $1", normalised,
        )
        if row is not None:
            return str(row["id"])
        new_id = str(uuid.uuid4())
        try:
            await conn.execute(
                "INSERT INTO projects (id, name, description) VALUES ($1, $2, $3)",
                new_id, normalised, description,
            )
        except asyncpg.UniqueViolationError as e:
            raise HTTPException(
                status_code=409,
                detail=f"project name {normalised!r} already exists",
            ) from e
        return new_id


async def attach_files_to_project(
    pool: asyncpg.Pool, project_id: str, refs: list[ProjectFileRef],
) -> int:
    """Insert N (project_id, neo4j_id) rows. Duplicates within this project
    are silently skipped (ON CONFLICT DO NOTHING) — the same file landing
    twice in the same project is harmless. Returns the number of new rows
    actually inserted."""
    if not refs:
        return 0
    rows = [
        (uuid.UUID(project_id), r.neo4j_id, r.source_type, r.file_name)
        for r in refs
    ]
    async with pool.acquire() as conn:
        result = await conn.executemany(
            """
            INSERT INTO project_files (project_id, neo4j_id, source_type, file_name)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (project_id, neo4j_id) DO NOTHING
            """,
            rows,
        )
        # executemany returns a status string per batch; we don't get a
        # cumulative rowcount cheaply. Re-count via a follow-up query.
        ids = [r.neo4j_id for r in refs]
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM project_files
            WHERE project_id = $1 AND neo4j_id = ANY($2::text[])
            """,
            uuid.UUID(project_id), ids,
        )
        return int(count)


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


def _require_pool() -> asyncpg.Pool:
    pool = get_pool()
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail="Postgres unreachable — project features unavailable",
        )
    return pool


@router.post("", response_model=dict)
async def create_project(req: ProjectCreate) -> dict[str, Any]:
    """Create a new project. 409 if the name is already taken — this route
    enforces strict uniqueness for explicit user-create. The internal
    ``get_or_create_project`` helper is idempotent for the upload flow."""
    pool = _require_pool()
    normalised = req.name.strip()
    if not normalised:
        raise HTTPException(status_code=400, detail="project name cannot be empty")
    new_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO projects (id, name, description)
                VALUES ($1, $2, $3)
                RETURNING id, name, description, created_at
                """,
                uuid.UUID(new_id), normalised, req.description,
            )
        except asyncpg.UniqueViolationError as e:
            raise HTTPException(
                status_code=409,
                detail=f"project name {normalised!r} already exists",
            ) from e
    assert row is not None
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row["description"],
        "created_at": row["created_at"].isoformat(),
    }


@router.get("", response_model=list[ProjectSummary])
async def list_projects() -> list[ProjectSummary]:
    """List all projects with file counts (overall + per source type)."""
    pool = _require_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                p.id, p.name, p.description, p.created_at,
                COUNT(pf.neo4j_id)                                                    AS file_count,
                COUNT(pf.neo4j_id) FILTER (WHERE pf.source_type = 'tableau')          AS c_tableau,
                COUNT(pf.neo4j_id) FILTER (WHERE pf.source_type = 'tws')              AS c_tws,
                COUNT(pf.neo4j_id) FILTER (WHERE pf.source_type = 'qlikview')         AS c_qlikview,
                COUNT(pf.neo4j_id) FILTER (WHERE pf.source_type = 'spark')            AS c_spark
            FROM projects p
            LEFT JOIN project_files pf ON pf.project_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """)
    return [
        ProjectSummary(
            id=str(r["id"]),
            name=r["name"],
            description=r["description"],
            created_at=r["created_at"].isoformat(),
            file_count=int(r["file_count"]),
            by_source={
                "tableau": int(r["c_tableau"]),
                "tws": int(r["c_tws"]),
                "qlikview": int(r["c_qlikview"]),
                "spark": int(r["c_spark"]),
            },
        )
        for r in rows
    ]


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(project_id: str) -> ProjectDetail:
    """Project detail — header + every file in it, grouped by source_type
    so the frontend can render per-parser subfolders without re-grouping."""
    pool = _require_pool()
    try:
        pid = uuid.UUID(project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid project_id") from e
    async with pool.acquire() as conn:
        header = await conn.fetchrow(
            "SELECT id, name, description, created_at FROM projects WHERE id = $1",
            pid,
        )
        if header is None:
            raise HTTPException(status_code=404, detail="project not found")
        rows = await conn.fetch(
            """
            SELECT neo4j_id, source_type, file_name, added_at
              FROM project_files
             WHERE project_id = $1
             ORDER BY source_type, added_at
            """,
            pid,
        )

    files = [
        ProjectFile(
            neo4j_id=r["neo4j_id"],
            source_type=r["source_type"],
            file_name=r["file_name"],
            added_at=r["added_at"].isoformat(),
        )
        for r in rows
    ]
    by_source: dict[str, list[ProjectFile]] = {}
    for f in files:
        by_source.setdefault(f.source_type, []).append(f)

    return ProjectDetail(
        id=str(header["id"]),
        name=header["name"],
        description=header["description"],
        created_at=header["created_at"].isoformat(),
        files=files,
        by_source=by_source,
    )


@router.delete("/{project_id}")
async def delete_project(project_id: str) -> dict[str, Any]:
    """Drop a project + its project_files rows. The underlying Neo4j file
    nodes are NOT touched — they survive so the file is still browsable
    via the source-type folders."""
    pool = _require_pool()
    try:
        pid = uuid.UUID(project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="invalid project_id") from e
    async with pool.acquire() as conn:
        res = await conn.execute("DELETE FROM projects WHERE id = $1", pid)
    if res.endswith(" 0"):
        raise HTTPException(status_code=404, detail="project not found")
    return {"deleted": True, "id": project_id}
