from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from tws_parser.api.routes import router
from tws_parser.graph import client as graph_client
from tws_parser.rdbms import client as rdbms_client
from tws_parser.utils.logging import configure as configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log = get_logger(__name__)
    log.info("tws_parser_starting")
    yield
    graph_client.close()
    rdbms_client.close()
    log.info("tws_parser_stopped")


app = FastAPI(
    title="TWS Parser",
    description="Parses IBM TWS schedule/job definitions into the lineage knowledge graph (Neo4j) + Postgres mirror",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
