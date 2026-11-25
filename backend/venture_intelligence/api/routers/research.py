"""Command search, research runs, and legacy investigations."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from venture_intelligence.api.routers.dependencies import _get_db
from venture_intelligence.config import settings
from venture_intelligence.pipeline.entity_integrity import is_generic_label
from venture_intelligence.programs import get_program_registry
from venture_intelligence.research.factory import build_research_agent
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    ResearchBudget,
    ResearchRun,
    ResearchTarget,
    ResearchTargetType,
)
from venture_intelligence.storage.db import Database

router = APIRouter(prefix="/api/v1", tags=["v1"])
legacy_router = APIRouter()


class ResearchRequest(BaseModel):
    entity_name: str = Field(min_length=1, max_length=200)
    entity_type: ResearchTargetType = ResearchTargetType.OTHER
    program_id: str | None = None
    aliases: list[str] = Field(default_factory=list, max_length=20)
    domains: list[str] = Field(default_factory=list, max_length=20)
    budget: ResearchBudget | None = None


class SearchSuggestion(BaseModel):
    id: str
    kind: str
    label: str
    context: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    route: str


@router.get("/search", response_model=list[SearchSuggestion])
async def command_search(q: str, limit: int = 12) -> list[SearchSuggestion]:
    """Search programs and integrity-validated canonical entities only."""
    query = q.strip()
    if not query:
        return []
    bounded = max(1, min(limit, 30))
    folded = query.casefold()
    terms = folded.split()
    suggestions: list[SearchSuggestion] = []
    for program in get_program_registry().active():
        program_index = " ".join(
            [
                program.id,
                program.canonical_program_id or "",
                program.name,
                program.batch,
                program.current_batch or "",
                *program.keywords,
                *(
                    value
                    for batch in program.supported_batches
                    for value in [batch.id, batch.name, *batch.keywords]
                ),
            ]
        ).casefold()
        if all(term in program_index for term in terms):
            suggestions.append(
                SearchSuggestion(
                    id=program.id,
                    kind="program",
                    label=program.name,
                    context=f"{program.batch} · {program.detection_strategy}",
                    route="/discover?type=programs",
                )
            )
    database = Database(settings.db_path)
    for item in database.search_validated_entities(query, limit=bounded):
        if is_generic_label(item["label"]):
            continue
        suggestions.append(
            SearchSuggestion(
                id=item["entity_id"],
                kind=item["entity_type"],
                label=item["label"],
                context=item["context"],
                confidence=item["confidence"],
                route=f"/research/{item['entity_id']}",
            )
        )
    return suggestions[:bounded]


@router.post("/research", response_model=ResearchRun)
async def research(request: ResearchRequest) -> ResearchRun:
    """Run bounded free/public research and publish typed findings to SQLite."""
    if not request.entity_name.strip():
        raise HTTPException(422, "entity_name must not be blank")
    registry = get_program_registry()
    program = registry.get(request.program_id) if request.program_id else None
    if request.program_id and program is None:
        raise HTTPException(404, f"Unknown program id: {request.program_id}")
    target = ResearchTarget(
        name=request.entity_name.strip(),
        type=request.entity_type,
        program_id=program.id if program else None,
        aliases=list(dict.fromkeys([*request.aliases, *([program.name] if program else [])])),
        domains=list(
            dict.fromkeys(
                [
                    *request.domains,
                    *(
                        urlsplit(url).netloc
                        for url in (program.website_urls if program else [])
                        if urlsplit(url).netloc
                    ),
                ]
            )
        ),
    )
    database = Database(settings.db_path)
    bus = EventBus(database)
    program = get_program_registry().get(request.program_id) if request.program_id else None
    agent = build_research_agent(
        program_name=program.name if program else None,
        program_batch=program.batch if program else None,
    )
    try:
        return await agent.run(target, budget=request.budget, event_bus=bus)
    finally:
        await agent.close()


@legacy_router.get("/api/investigations/{entity_id}")
async def investigation(entity_id: str) -> dict:
    """Return the latest human-review investigation for an entity."""
    return {"entity_id": entity_id, "investigation": _get_db().get_investigation(entity_id)}


@legacy_router.get("/api/investigations")
async def investigations(limit: int = 50) -> dict:
    """Return recent human-review investigations for legacy clients."""
    db = _get_db()
    bounded = max(1, min(limit, 100))
    db._ensure_investigation_table()
    with db._connect() as connection:
        rows = connection.execute(
            "SELECT * FROM investigations ORDER BY updated_at DESC LIMIT ?", (bounded,)
        ).fetchall()
    return {"investigations": [dict(row) for row in rows]}
