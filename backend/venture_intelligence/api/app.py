"""FastAPI application and legacy dashboard-compatible routes.

This module is the app factory: it creates the app, wires middleware, shared
state, and static files, and includes the domain routers from
``venture_intelligence.api.routers`` in the original route-matching order.
Names previously defined here stay importable and patchable through a
forwarding facade.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from venture_intelligence.api.routers import analyst as analyst_routes
from venture_intelligence.api.routers import decisions as decisions_routes
from venture_intelligence.api.routers import dependencies
from venture_intelligence.api.routers import evaluation as evaluation_routes
from venture_intelligence.api.routers import events as events_routes
from venture_intelligence.api.routers import founders as founders_routes
from venture_intelligence.api.routers import frontend as frontend_routes
from venture_intelligence.api.routers import gather as gather_routes
from venture_intelligence.api.routers import graph as graph_routes
from venture_intelligence.api.routers import knowledge as knowledge_routes
from venture_intelligence.api.routers import notifications as notifications_routes
from venture_intelligence.api.routers import operations as operations_routes
from venture_intelligence.api.routers import ops as ops_routes
from venture_intelligence.api.routers import programs as programs_routes
from venture_intelligence.api.routers import research as research_routes
from venture_intelligence.api.routers import sources as sources_routes
from venture_intelligence.api.routers import websocket as websocket_routes
from venture_intelligence.api.routers._compat import forward_missing_attributes
from venture_intelligence.api.routers.batch_discovery import router as batch_discovery_router
from venture_intelligence.api.routers.v1 import router as v1_router
from venture_intelligence.config import settings
from venture_intelligence.crm.workflow import CRMWorkflow
from venture_intelligence.storage.db import Database

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    dependencies._db = Database(settings.db_path)
    dependencies._crm = CRMWorkflow(dependencies._db)
    logger.info("venture-intelligence API started db=%s", settings.db_path)
    yield
    logger.info("venture-intelligence API stopped")


app = FastAPI(
    title="Venture Intelligence",
    description="Configuration-driven, evidence-oriented venture intelligence.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure a narrow origin list before remote deployment.
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)
app.include_router(v1_router)
app.include_router(batch_discovery_router)
app.mount("/frontend", StaticFiles(directory=frontend_routes._frontend_root), name="frontend")

# Unversioned dashboard routes. Include order is route-matching order: keep it.
app.include_router(events_routes.router)
app.include_router(evaluation_routes.legacy_router)
app.include_router(analyst_routes.router)
app.include_router(frontend_routes.router)
app.include_router(ops_routes.router)
app.include_router(founders_routes.legacy_router)
app.include_router(decisions_routes.legacy_router)
app.include_router(programs_routes.legacy_router)
app.include_router(ops_routes.metrics_router)
app.include_router(knowledge_routes.legacy_router)
app.include_router(notifications_routes.router)
app.include_router(research_routes.legacy_router)
app.include_router(graph_routes.legacy_router)
app.include_router(founders_routes.companies_router)
app.include_router(websocket_routes.router)
app.include_router(frontend_routes.fallback_router)  # catch-all; must stay last

forward_missing_attributes(
    __name__,
    dependencies,
    events_routes,
    evaluation_routes,
    analyst_routes,
    frontend_routes,
    ops_routes,
    founders_routes,
    decisions_routes,
    programs_routes,
    knowledge_routes,
    notifications_routes,
    research_routes,
    graph_routes,
    websocket_routes,
    sources_routes,
    operations_routes,
    gather_routes,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("venture_intelligence.api.app:app", host=settings.api_host, port=settings.api_port)
