"""Health, statistics, and metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter
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
