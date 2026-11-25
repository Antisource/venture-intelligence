"""Legacy canonical event feed for dashboard consumers."""

from __future__ import annotations

from fastapi import APIRouter

from venture_intelligence.api.routers.dependencies import _get_db

router = APIRouter()


@router.get("/api/events")
async def events(limit: int = 50) -> dict:
    """Return recent canonical events for legacy dashboard consumers."""
    db = _get_db()
    bounded = max(1, min(limit, 100))
    with db._connect() as connection:
        rows = connection.execute(
            "SELECT * FROM events ORDER BY ingested_at DESC LIMIT ?", (bounded,)
        ).fetchall()
    return {"events": [db._row_to_event(row).model_dump(mode="json") for row in rows]}
