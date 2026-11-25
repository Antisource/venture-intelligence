"""Versioned intelligence API, independent of the dashboard frontend.

The ``/api/v1`` handlers live in domain modules; ``router`` includes their
versioned routers in the original route-matching order.  Names previously
defined here stay importable and patchable through a forwarding facade.
"""

from __future__ import annotations

from fastapi import APIRouter

from venture_intelligence.api.routers import decisions as decisions_routes
from venture_intelligence.api.routers import evaluation as evaluation_routes
from venture_intelligence.api.routers import founders as founders_routes
from venture_intelligence.api.routers import gather as gather_routes
from venture_intelligence.api.routers import graph as graph_routes
from venture_intelligence.api.routers import knowledge as knowledge_routes
from venture_intelligence.api.routers import operations as operations_routes
from venture_intelligence.api.routers import programs as programs_routes
from venture_intelligence.api.routers import research as research_routes
from venture_intelligence.api.routers import sources as sources_routes
from venture_intelligence.api.routers._compat import forward_missing_attributes

# Include order is route-matching order: keep it.
router = APIRouter()
router.include_router(knowledge_routes.router)
router.include_router(programs_routes.router)
router.include_router(sources_routes.router)
router.include_router(operations_routes.router)
router.include_router(evaluation_routes.replay_router)
router.include_router(graph_routes.router)
router.include_router(evaluation_routes.router)
router.include_router(founders_routes.router)
router.include_router(decisions_routes.router)
router.include_router(research_routes.router)
router.include_router(gather_routes.router)

forward_missing_attributes(
    __name__,
    knowledge_routes,
    programs_routes,
    sources_routes,
    operations_routes,
    evaluation_routes,
    graph_routes,
    founders_routes,
    decisions_routes,
    research_routes,
    gather_routes,
)
