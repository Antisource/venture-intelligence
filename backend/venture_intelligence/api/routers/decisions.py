"""CRM workflow and partner decision surfaces (versioned and legacy)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from venture_intelligence.api.routers.dependencies import _get_crm, _get_db
from venture_intelligence.config import settings
from venture_intelligence.decisions.models import (
    CRMActionResult,
    DecisionBoardResponse,
    DecisionEntityResponse,
)
from venture_intelligence.decisions.service import DecisionService
from venture_intelligence.pipeline.entity_integrity import validated_entity_record
from venture_intelligence.programs import get_program_registry
from venture_intelligence.shared.models import (
    AlertLifecycleEvent,
    AlertLifecycleStatus,
    CRMActionType,
    CRMStage,
    RecommendationLifecycleEvent,
    RecommendationLifecycleStatus,
)
from venture_intelligence.storage.db import Database

router = APIRouter(prefix="/api/v1", tags=["v1"])
legacy_router = APIRouter()


class CRMActionRequest(BaseModel):
    action: CRMActionType
    partner: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=4_000)
    to_stage: CRMStage | None = None
    occurred_at: datetime | None = None
    provenance: dict[str, object] = Field(default_factory=dict)
    action_id: str | None = Field(default=None, min_length=1, max_length=200)


class RecommendationLifecycleRequest(BaseModel):
    status: RecommendationLifecycleStatus
    actor: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=4_000)


class AlertLifecycleRequest(BaseModel):
    status: AlertLifecycleStatus
    actor: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=4_000)


@router.get("/decisions", response_model=DecisionBoardResponse)
async def decision_board(
    as_of: datetime | None = None,
    program_id: str | None = None,
) -> DecisionBoardResponse:
    """Return the integrity-gated, historically bounded CRM decision board."""
    try:
        if program_id and get_program_registry().get(program_id) is None:
            raise HTTPException(404, f"Unknown program id: {program_id}")
        return DecisionService(Database(settings.db_path)).board(as_of=as_of, program_id=program_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get(
    "/decisions/entities/{entity_id}",
    response_model=DecisionEntityResponse,
)
async def decision_entity(
    entity_id: str,
    as_of: datetime | None = None,
) -> DecisionEntityResponse:
    """Return one entity's CRM actions, feedback lineage, and decision lifecycle."""
    try:
        result = DecisionService(Database(settings.db_path)).entity(entity_id, as_of=as_of)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if result is None:
        raise HTTPException(404, "Validated CRM decision entity not found")
    return result


@router.post(
    "/decisions/entities/{entity_id}/actions",
    response_model=CRMActionResult,
)
async def record_crm_action(
    entity_id: str,
    request: CRMActionRequest,
) -> CRMActionResult:
    """Append human feedback and deterministically rebuild downstream lineage."""
    try:
        return DecisionService(Database(settings.db_path)).apply_action(
            entity_id,
            request.action,
            partner=request.partner,
            note=request.note,
            to_stage=request.to_stage,
            occurred_at=request.occurred_at,
            provenance={"channel": "api", **request.provenance},
            action_id=request.action_id,
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post(
    "/decisions/recommendations/{recommendation_id}/lifecycle",
    response_model=RecommendationLifecycleEvent,
)
async def update_recommendation_lifecycle(
    recommendation_id: str,
    request: RecommendationLifecycleRequest,
) -> RecommendationLifecycleEvent:
    allowed = {
        RecommendationLifecycleStatus.ACCEPTED,
        RecommendationLifecycleStatus.DISMISSED,
        RecommendationLifecycleStatus.COMPLETED,
    }
    if request.status not in allowed:
        raise HTTPException(422, "Only human decision lifecycle states may be posted")
    try:
        return DecisionService(Database(settings.db_path)).update_recommendation(
            recommendation_id,
            request.status,
            actor=request.actor,
            reason=request.reason,
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post(
    "/decisions/alerts/{alert_id}/lifecycle",
    response_model=AlertLifecycleEvent,
)
async def update_alert_lifecycle(
    alert_id: str,
    request: AlertLifecycleRequest,
) -> AlertLifecycleEvent:
    allowed = {
        AlertLifecycleStatus.ACKNOWLEDGED,
        AlertLifecycleStatus.DISMISSED,
        AlertLifecycleStatus.RESOLVED,
    }
    if request.status not in allowed:
        raise HTTPException(422, "Only human alert lifecycle states may be posted")
    try:
        return DecisionService(Database(settings.db_path)).update_alert(
            alert_id,
            request.status,
            actor=request.actor,
            reason=request.reason,
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


class AdvanceRequest(BaseModel):
    to_stage: str
    partner: str | None = None
    note: str | None = None


class NoteRequest(BaseModel):
    note: str
    partner: str | None = None


@legacy_router.get("/api/crm")
async def crm_board() -> dict:
    db = _get_db()
    board = _get_crm().get_board()
    return {
        stage: [
            _crm_record_view(record, db)
            for record in records
            if _is_valid_canonical_entity(record.get("entity_id"))
        ]
        for stage, records in board.items()
    }


@legacy_router.post("/api/crm/{entity_id}/advance")
async def advance_crm(entity_id: str, request: AdvanceRequest) -> dict:
    if not _is_valid_canonical_entity(entity_id):
        raise HTTPException(404, "Validated canonical entity not found")
    try:
        record = _get_crm().advance_stage(
            entity_id, CRMStage(request.to_stage), request.partner, request.note
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"status": "ok", "stage": record.stage, "entity_id": entity_id}


@legacy_router.post("/api/crm/{entity_id}/note")
async def add_note(entity_id: str, request: NoteRequest) -> dict:
    if not _is_valid_canonical_entity(entity_id):
        raise HTTPException(404, "Validated canonical entity not found")
    record = _get_crm().add_note(entity_id, request.note, request.partner)
    return {"status": "ok", "notes": record.notes}


def _is_valid_canonical_entity(entity_id: str | None) -> bool:
    if not entity_id:
        return False
    db = _get_db()
    entity = db.get_company(entity_id) or db.get_person(entity_id)
    return bool(entity and db.is_entity_validated(entity_id) and validated_entity_record(entity))


def _crm_record_view(record: dict, db: Database) -> dict:
    """Join the workflow record to canonical evidence without duplicating it."""
    entity_id = record["entity_id"]
    entity = db.get_company(entity_id) or db.get_person(entity_id)
    assert entity is not None  # checked by the caller
    evidence = db.get_evidence_for_entity(entity_id)
    events = db.get_events_for_entity(entity_id)
    result = dict(record)
    result.update(
        {
            "canonical_name": entity.canonical_name,
            "entity_type": "company" if db.get_company(entity_id) else "person",
            "website": getattr(entity, "website", None)
            or getattr(entity, "yc_url", None)
            or getattr(entity, "linkedin_url", None),
            "confidence": entity.identity_confidence,
            "evidence_count": len(evidence),
            "source_urls": list(
                dict.fromkeys(event.source_url for event in events if event.source_url)
            ),
            "next_action": "Review evidence and advance when conviction changes.",
        }
    )
    return result
