"""Health, probes, statistics, and metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from venture_intelligence.api.routers.dependencies import _get_db
from venture_intelligence.config import settings

router = APIRouter()
metrics_router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    project: str
    db: str


@router.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        project="venture-intelligence",
        db=settings.db_path,
    )


# Container and orchestrator probes (ADR-0004 §4). They stay out of the OpenAPI
# document, which is the frontend's typed contract, not an ops surface.
@router.get("/livez", include_in_schema=False)
async def livez() -> dict:
    """The process is up. Never touches a datastore."""
    return {"status": "alive"}


@router.get("/readyz", include_in_schema=False)
def readyz() -> JSONResponse:
    """Datastores are reachable and Postgres (when configured) is at head.

    A plain ``def`` so the blocking checks run in the threadpool, not on the
    event loop (ADR-0001 §3). Readiness never writes or migrates.
    """
    from venture_intelligence.storage.pg.health import readiness

    ready, payload = readiness(settings)
    return JSONResponse(payload, status_code=200 if ready else 503)


@router.get("/api/stats")
async def stats() -> dict:
    db = _get_db()
    with db._connect() as connection:
        return {
            "total_events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "total_companies": connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
            "total_persons": connection.execute("SELECT COUNT(*) FROM persons").fetchone()[0],
            "total_signals": connection.execute("SELECT COUNT(*) FROM signals").fetchone()[0],
            "total_alerts": connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0],
        }


@metrics_router.get("/api/metrics")
async def metrics() -> dict:
    from venture_intelligence.observability.metrics import metrics as collector

    return collector.snapshot()
