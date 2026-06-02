from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from tableau_parser.api.routes import router
from tableau_parser.graph import client
from tableau_parser.utils.logging import configure as configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log = get_logger(__name__)
    log.info("tableau_parser_starting")
    yield
    client.close()
    log.info("tableau_parser_stopped")


app = FastAPI(
    title="Tableau Parser",
    description="Parses Tableau .twb / .twbx workbooks into the lineage knowledge graph",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
