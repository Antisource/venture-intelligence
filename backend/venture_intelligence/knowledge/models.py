"""Typed contracts for the filesystem-backed knowledge layer."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ClaimStatus(str, Enum):
    CONFIRMED = "confirmed"
    SUPPORTED = "supported"
    INFERRED = "inferred"
    HISTORICAL = "historical"
    STALE = "stale"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class KnowledgeClaim(BaseModel):
    claim_id: str
    statement: str
    status: ClaimStatus = ClaimStatus.UNKNOWN
    confidence: float = Field(default=0.0, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class KnowledgePage(BaseModel):
    schema_version: str = "1.0"
    entity_type: str
    entity_id: str
    identity: dict[str, Any] = Field(default_factory=dict)
    known_facts: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[KnowledgeClaim] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    evidence_by_category: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    category_confidence: dict[str, dict[str, Any]] = Field(default_factory=dict)
    overall_confidence_pct: int = Field(default=0, ge=0, le=100)
    overall_confidence_label: str = "unknown"
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    historical_information: list[dict[str, Any]] = Field(default_factory=list)
    contradictions: list[dict[str, Any]] = Field(default_factory=list)
    related_entities: list[dict[str, Any]] = Field(default_factory=list)
    revisions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
