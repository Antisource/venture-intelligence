"""Bounded, best-effort on-demand intelligence gathering."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from venture_intelligence.config import settings
from venture_intelligence.connectors.factory import build_connectors
from venture_intelligence.pipeline.entity_integrity import validate_candidate
from venture_intelligence.programs import get_program_registry
from venture_intelligence.research.provider_policy import SourceOperationStatus
from venture_intelligence.shared.models import CanonicalEvent, EntityType, EventType, Source

router = APIRouter(prefix="/api/v1", tags=["v1"])


class GatherRequest(BaseModel):
    entity_name: str = Field(min_length=1, max_length=200)
    program_id: str | None = None


class GatherSourceStatus(str, Enum):
    SUCCESS = "SUCCESS"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    SKIPPED = "SKIPPED"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    BLOCKED = "BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class GatherSourceOutcome(BaseModel):
    source: str
    attempted: bool
    status: GatherSourceStatus
    events_found: int = 0
    events_accepted: int = 0
    events_rejected: int = 0
    error_category: str | None = None
    final_url: str | None = None
    http_status: int | None = None
    normalized_status: SourceOperationStatus | None = None

    @model_validator(mode="after")
    def _normalize_status(self) -> GatherSourceOutcome:
        if self.normalized_status is not None:
            return self
        self.normalized_status = {
            GatherSourceStatus.SUCCESS: SourceOperationStatus.SUCCESS,
            GatherSourceStatus.SUCCESS_EMPTY: SourceOperationStatus.EMPTY,
            GatherSourceStatus.SKIPPED: SourceOperationStatus.SKIPPED,
            GatherSourceStatus.CANDIDATE_REJECTED: SourceOperationStatus.CANDIDATE_REJECTED,
            GatherSourceStatus.TIMEOUT: SourceOperationStatus.TIMEOUT,
            GatherSourceStatus.FAILED: SourceOperationStatus.FAILED,
            GatherSourceStatus.AUTH_REQUIRED: SourceOperationStatus.AUTH_REQUIRED,
            GatherSourceStatus.BLOCKED: SourceOperationStatus.BLOCKED,
            GatherSourceStatus.RATE_LIMITED: SourceOperationStatus.RATE_LIMITED,
            GatherSourceStatus.TEMPORARILY_UNAVAILABLE: (
                SourceOperationStatus.TEMPORARILY_UNAVAILABLE
            ),
            GatherSourceStatus.UNAVAILABLE: SourceOperationStatus.UNAVAILABLE,
            GatherSourceStatus.BUDGET_EXHAUSTED: SourceOperationStatus.BUDGET_EXHAUSTED,
        }[self.status]
        return self


class GatherResponse(BaseModel):
    entity_name: str
    mode: str = "preview_corroboration"
    persisted_events: int = 0
    sources_considered: list[str]
    sources_attempted: list[str]
    outcomes: list[GatherSourceOutcome]
    events_found: int
    events_accepted: int
    events_rejected: int
    gathered_at: datetime


@router.post("/intel/gather", response_model=GatherResponse)
async def gather_intelligence(request: GatherRequest) -> GatherResponse:
    """Preview a bounded public corroboration pass without persistence."""
    registry = get_program_registry()
    program = registry.get(request.program_id) if request.program_id else None
    if request.program_id and program is None:
        raise HTTPException(404, f"Unknown program id: {request.program_id}")

    entity_name = request.entity_name.strip()
    if not entity_name:
        raise HTTPException(422, "entity_name must not be blank")
    entity_id = (
        registry.entity_id(program.id, entity_name) if program else f"entity__{_slug(entity_name)}"
    )
    trigger = CanonicalEvent(
        event_type=EventType.FOUNDER_DETECTED,
        entity_type=EntityType.COMPANY,
        entity_id=entity_id,
        source=Source.API,
        source_url="api://v1/intel/gather",
        occurred_at=datetime.now(timezone.utc),
        confidence=0.5,
        raw_payload={"name": entity_name},
        metadata={"entity_name": entity_name, "program_id": program.id if program else None},
    )
    connectors = build_connectors(registry, share_crawler=False)
    eligible = [connector for connector in connectors if connector.can_investigate(trigger)]
    operation_ids: dict[str, str | None] = {}
    tasks = {
        asyncio.create_task(_run_gather_operation(connector, trigger, operation_ids)): connector
        for connector in eligible
    }
    completed: dict[str, list[CanonicalEvent] | Exception | None] = {}
    try:
        done, pending = await asyncio.wait(
            tasks,
            timeout=settings.gather_timeout_seconds,
        )
        for task in done:
            connector = tasks[task]
            try:
                completed[connector.source_name] = task.result()
            except Exception as error:
                completed[connector.source_name] = error
        for task in pending:
            task.cancel()
            completed[tasks[task].source_name] = None
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await asyncio.gather(
            *(connector.close() for connector in connectors),
            return_exceptions=True,
        )

    source_outcomes = [
        _gather_source_outcome(
            connector,
            completed.get(connector.source_name),
            operation_id=operation_ids.get(connector.source_name),
        )
        for connector in eligible
    ]
    return GatherResponse(
        entity_name=entity_name,
        sources_considered=[item.source for item in source_outcomes],
        sources_attempted=[item.source for item in source_outcomes if item.attempted],
        outcomes=source_outcomes,
        events_found=sum(item.events_found for item in source_outcomes),
        events_accepted=sum(item.events_accepted for item in source_outcomes),
        events_rejected=sum(item.events_rejected for item in source_outcomes),
        gathered_at=datetime.now(timezone.utc),
    )


def _gather_source_outcome(
    connector,
    result: list[CanonicalEvent] | Exception | None,
    *,
    operation_id: str | None = None,
) -> GatherSourceOutcome:
    crawler = getattr(connector, "crawler", None)
    attempted = bool(getattr(crawler, "request_count", 0))
    crawl_outcomes = list(getattr(crawler, "request_outcomes", []))
    latest = crawl_outcomes[-1] if crawl_outcomes else {}
    recorder = getattr(crawler, "record_evidence_outcome", None)
    if recorder is not None:
        evidence_urls = (event.source_url for event in result) if isinstance(result, list) else ()
        if operation_id is None:
            recorder(evidence_urls)
        else:
            recorder(evidence_urls, operation_id=operation_id)
    if result is None:
        return GatherSourceOutcome(
            source=connector.source_name,
            attempted=attempted,
            status=GatherSourceStatus.TIMEOUT,
            error_category="timeout",
            final_url=latest.get("url"),
            http_status=latest.get("status_code"),
        )
    if isinstance(result, Exception):
        return GatherSourceOutcome(
            source=connector.source_name,
            attempted=attempted,
            status=GatherSourceStatus.FAILED,
            error_category=result.__class__.__name__,
            final_url=latest.get("url"),
            http_status=latest.get("status_code"),
        )

    validations = [validate_candidate(event) for event in result]
    accepted = sum(item.accepted for item in validations)
    rejected = len(validations) - accepted
    rejection_reasons = sorted(
        {item.reason for item in validations if not item.accepted and item.reason}
    )
    if accepted:
        status = GatherSourceStatus.SUCCESS
        error_category = None
    elif rejected:
        status = GatherSourceStatus.CANDIDATE_REJECTED
        error_category = ",".join(rejection_reasons) or "candidate_rejected"
    elif not attempted:
        status = GatherSourceStatus.SKIPPED
        error_category = "not_applicable"
    elif latest.get("usable"):
        status = GatherSourceStatus.SUCCESS_EMPTY
        error_category = None
    else:
        status = str(latest.get("source_status") or GatherSourceStatus.FAILED.value)
        error_category = str(latest.get("error") or "source_unavailable")
    return GatherSourceOutcome(
        source=connector.source_name,
        attempted=attempted,
        status=status,
        events_found=len(result),
        events_accepted=accepted,
        events_rejected=rejected,
        error_category=error_category,
        final_url=latest.get("url"),
        http_status=latest.get("status_code"),
    )


async def _run_gather_operation(connector, trigger, operation_ids):
    """Investigate with request outcomes attributed to this connector task."""
    crawler = getattr(connector, "crawler", None)
    begin = getattr(crawler, "begin_evidence_operation", None)
    end = getattr(crawler, "end_evidence_operation", None)
    operation_id = None
    token = None
    if begin is not None and end is not None:
        operation_id, token = begin()
    operation_ids[connector.source_name] = operation_id
    try:
        try:
            return await connector.investigate(trigger)
        except Exception as exc:
            return exc
    finally:
        if token is not None:
            end(token)


def _slug(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "unknown"
