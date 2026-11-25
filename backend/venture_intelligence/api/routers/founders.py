"""Founder, company, and ranking surfaces (versioned and legacy)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from venture_intelligence.api.routers.dependencies import _accepts_html, _get_db
from venture_intelligence.config import settings
from venture_intelligence.intelligence.models import (
    EntityIntelligenceResponse,
    RankingIntelligenceResponse,
)
from venture_intelligence.intelligence.service import IntelligenceService
from venture_intelligence.knowledge.compiler import KnowledgeCompiler
from venture_intelligence.knowledge.evidence import normalize_evidence, summarize_evidence
from venture_intelligence.pipeline.entity_integrity import validated_entity_record
from venture_intelligence.programs import get_program_registry
from venture_intelligence.storage.db import Database

router = APIRouter(prefix="/api/v1", tags=["v1"])
legacy_router = APIRouter()
companies_router = APIRouter()


@router.get(
    "/intelligence/rankings",
    response_model=RankingIntelligenceResponse,
)
async def ranking_intelligence(
    as_of: datetime | None = None,
    entity_type: str | None = None,
    program_id: str | None = None,
    batch_id: str | None = None,
    limit: int = 100,
) -> RankingIntelligenceResponse:
    """Return integrity-gated rankings with exact feature/signal lineage."""
    if entity_type not in {None, "person", "company"}:
        raise HTTPException(422, "entity_type must be 'person' or 'company'")
    if program_id and get_program_registry().get(program_id) is None:
        raise HTTPException(404, f"Unknown program id: {program_id}")
    try:
        return IntelligenceService(Database(settings.db_path)).rankings(
            as_of=as_of,
            entity_type=entity_type,
            program_id=program_id,
            batch_id=batch_id,
            limit=max(1, min(limit, 200)),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get(
    "/intelligence/entities/{entity_id}",
    response_model=EntityIntelligenceResponse,
)
async def entity_intelligence(
    entity_id: str,
    as_of: datetime | None = None,
) -> EntityIntelligenceResponse:
    """Return one validated entity's evidence-to-recommendation lineage."""
    try:
        result = IntelligenceService(Database(settings.db_path)).entity(
            entity_id,
            as_of=as_of,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if result is None:
        raise HTTPException(404, "Validated entity intelligence not found")
    return result


@legacy_router.get("/api/founders")
async def list_founders(request: Request, limit: int = 50, offset: int = 0):
    if request.url.path == "/founders" and _accepts_html(request):
        return RedirectResponse("/#/founders", status_code=307)
    return _list_founders_payload(limit, offset)


def _list_founders_payload(limit: int, offset: int) -> dict:
    db = _get_db()
    rankings = db.get_rankings(limit=max(1, min(limit, 100)) + max(offset, 0))[offset:]
    results = []
    for ranking in rankings:
        entity = db.get_company(ranking.entity_id) or db.get_person(ranking.entity_id)
        if (
            not entity
            or not db.is_entity_validated(ranking.entity_id)
            or not validated_entity_record(entity)
        ):
            continue
        results.append(
            {
                "entity_id": ranking.entity_id,
                "name": entity.canonical_name if entity else ranking.entity_id,
                "rank": ranking.rank,
                "composite_score": ranking.composite_score,
                "confidence": ranking.confidence,
                "reasoning": ranking.reasoning,
            }
        )
    return {"total": len(results), "results": results}


@legacy_router.get("/api/review-queue")
async def review_queue(limit: int = 50, offset: int = 0) -> dict:
    """Explicit integrity-gated alias for the ranked review queue."""
    return _list_founders_payload(limit, offset)


@legacy_router.get("/api/founders/{entity_id}")
async def founder_detail(request: Request, entity_id: str):
    if request.url.path.startswith("/founders/") and _accepts_html(request):
        return RedirectResponse(f"/#/founders/{entity_id}", status_code=307)
    db = _get_db()
    entity = db.get_company(entity_id) or db.get_person(entity_id)
    if not entity or not db.is_entity_validated(entity_id) or not validated_entity_record(entity):
        raise HTTPException(404, f"Entity {entity_id!r} not found")
    ranking = db.get_ranking_for_entity(entity_id)
    return {
        "entity": entity.model_dump(mode="json"),
        "ranking": ranking.model_dump(mode="json") if ranking else None,
        "signals": [item.model_dump(mode="json") for item in db.get_signals_for_entity(entity_id)],
        "evidence": [
            item.model_dump(mode="json") for item in db.get_evidence_for_entity(entity_id)
        ],
        "events": [item.model_dump(mode="json") for item in db.get_events_for_entity(entity_id)],
    }


@legacy_router.get("/api/alerts")
async def alerts(limit: int = 50) -> dict:
    return {"alerts": _get_db().get_recent_alerts(limit=max(1, min(limit, 100)))}


@companies_router.get("/api/companies/evidence")
async def company_evidence(limit: int = 100) -> dict:
    """Canonical, integrity-gated rows for the interactive company evidence table."""
    db = _get_db()
    rows: list[dict] = []
    for company in db.all_companies():
        if not db.is_entity_validated(company.company_id) or not validated_entity_record(company):
            continue
        evidence = db.get_evidence_details_for_entity(company.company_id)
        events = db.get_events_for_entity(company.company_id)
        evidence_summary = summarize_evidence(evidence)
        sources = list(
            dict.fromkeys(url for item in evidence for url in item.get("source_urls", []) if url)
        )
        sources.extend(
            event.source_url
            for event in events
            if event.source_url and event.source_url not in sources
        )
        founder_ids = list(company.founder_ids)
        if not founder_ids:
            with db._connect() as connection:
                founder_ids = [
                    row["source_id"]
                    for row in connection.execute(
                        """
                        SELECT source_id FROM graph_edges
                        WHERE target_id=? AND edge_type='FOUNDED'
                        ORDER BY observed_at DESC
                        """,
                        (company.company_id,),
                    ).fetchall()
                ]
        founders = [db.get_person(founder_id) for founder_id in founder_ids]
        founder_details = []
        for person in founders:
            if person is None or not validated_entity_record(person):
                continue
            parts = person.canonical_name.split()
            founder_evidence = db.get_evidence_details_for_entity(person.person_id)
            founder_details.append(
                {
                    "person_id": person.person_id,
                    "full_name": person.canonical_name,
                    "first_name": parts[0] if parts else None,
                    "last_name": parts[-1] if len(parts) > 1 else None,
                    "founder_role": person.properties.get("founder_role")
                    or person.properties.get("role")
                    or "Founder",
                    "is_founder": bool(person.properties.get("is_founder", True)),
                    "confidence": person.identity_confidence,
                    "evidence": [normalize_evidence(item) for item in founder_evidence],
                    "source_urls": list(
                        dict.fromkeys(
                            url
                            for item in founder_evidence
                            for url in item.get("source_urls", [])
                            if url
                        )
                    ),
                }
            )
        knowledge = KnowledgeCompiler(settings.knowledge_dir).find(company.company_id)
        rows.append(
            {
                "entity_id": company.company_id,
                "company": company.canonical_name,
                "founders": [item["full_name"] for item in founder_details],
                "founder_details": founder_details,
                "website": company.website or company.yc_url,
                "evidence_count": len(evidence),
                "founder_background": "Unknown / insufficient public evidence",
                "confidence": company.identity_confidence,
                "sources": sources,
                "evidence": [normalize_evidence(item) for item in evidence],
                "evidence_by_category": evidence_summary["categories"],
                "evidence_categories": evidence_summary["categories"],
                "category_confidence": evidence_summary["category_confidence"],
                "overall_confidence": evidence_summary["overall_confidence"],
                "overall_confidence_pct": evidence_summary["overall_confidence_pct"],
                "knowledge_revision": KnowledgeCompiler.polling_metadata(knowledge),
            }
        )
    return {"results": rows[: max(1, min(limit, 100))]}


@companies_router.get("/api/candidates/rejections")
async def rejected_candidates(limit: int = 100) -> dict:
    """Audit-only candidate lifecycle records; these are never entities."""
    return {"rejections": _get_db().get_candidate_rejections(max(1, min(limit, 100)))}
