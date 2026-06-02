"""FastAPI entry point — wires routers, lifespan, CORS."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__, neo4j_client, postgres_client
from .config import get_settings
from .files_routes import router as files_router
from .graph_routes import router as graph_router
from .parse_proxy import router as parse_router
from .projects import ensure_schema as ensure_projects_schema
from .projects import router as projects_router
from .tws_routes import router as tws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    await neo4j_client.init_driver(settings)
    await postgres_client.init_pool(settings)
    # Idempotent schema bootstrap for projects + project_files. ``init.sql``
    # only runs on a fresh volume; this lets the projects feature land on
    # existing deployments without a manual migration.
    try:
        await ensure_projects_schema()
    except Exception as e:
        logging.warning("projects schema bootstrap failed: %s", e)
    try:
        yield
    finally:
        await neo4j_client.close_driver()
        await postgres_client.close_pool()


app = FastAPI(
    title="Lineage Platform — Gateway",
    version=__version__,
    description="Aggregation API for the multi-parser knowledge graph.",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Gateway liveness — reports downstream-store reachability."""
    status = {"status": "ok", "neo4j": "connected", "postgres": "connected"}
    try:
        async with neo4j_client.session() as s:
            await (await s.run("RETURN 1")).consume()
    except Exception:
        status["neo4j"] = "unreachable"
        status["status"] = "degraded"
    pool = postgres_client.get_pool()
    if pool is None:
        status["postgres"] = "unreachable"
        status["status"] = "degraded"
    else:
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception:
            status["postgres"] = "unreachable"
            status["status"] = "degraded"
    return status


@app.get("/version")
def version() -> dict[str, str]:
    return {"gateway": "lineage-gateway", "version": __version__}


app.include_router(graph_router)
app.include_router(tws_router)
app.include_router(parse_router)
app.include_router(files_router)
app.include_router(projects_router)
