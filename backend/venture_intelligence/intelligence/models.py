"""Authoritative API contracts for M4 entity and ranking intelligence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from venture_intelligence.shared.models import (
    CRMEvent,
    Feature,
    FounderRanking,
    Hypothesis,
    Recommendation,
    Signal,
)


class EntitySummary(BaseModel):
    entity_id: str
    entity_type: Literal["person", "company"]
    canonical_name: str
    identity_confidence: float = Field(ge=0, le=1)
    validation_status: Literal["validated"] = "validated"
    identity_status: Literal[
        "verified", "corroborated", "ambiguous", "provisional", "unresolved"
    ] = "unresolved"
    program_status: Literal[
        "verified", "supported", "hypothesis", "unknown", "contradicted"
    ] = "unknown"
    independent_evidence_origins: int = Field(default=0, ge=0)
    website: str | None = None
    current_title: str | None = None
    current_company_id: str | None = None
    program_id: str | None = None
    batch_id: str | None = None
    founder_ids: list[str] = Field(default_factory=list)


class EvidenceLineageRecord(BaseModel):
    evidence_id: str
    claim: str
    confidence: float = Field(ge=0, le=1)
    observed_at: datetime
    observation_ids: list[str]
    source_urls: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    program_id: str | None = None
    batch_id: str | None = None
    attribution_status: Literal["attached", "unbound", "contradictory"] = "attached"
    attribution_reason: str | None = None
    evidence_origin_url: str | None = None
    acquisition_providers: list[str] = Field(default_factory=list)
    occurred_at: datetime | None = None
    published_at: datetime | None = None


class LineageEdge(BaseModel):
    from_id: str
    from_type: Literal[
        "observation",
        "evidence",
        "crm_action",
        "feature",
        "signal",
        "hypothesis",
        "ranking",
    ]
    to_id: str
    to_type: Literal["evidence", "feature", "signal", "hypothesis", "ranking", "recommendation"]
    relation: str


class RankingMovement(BaseModel):
    current_rank: int | None = None
    previous_rank: int | None = None
    rank_delta: int | None = None
    score_delta: float | None = None


class RankingIntelligenceItem(BaseModel):
    entity: EntitySummary
    ranking: FounderRanking
    movement: RankingMovement
    top_signal: Signal | None = None
    top_feature: Feature | None = None


class RankingIntelligenceResponse(BaseModel):
    schema_version: str = "4.0"
    as_of: datetime
    program_id: str | None = None
    batch_id: str | None = None
    items: list[RankingIntelligenceItem]


class EntityIntelligenceResponse(BaseModel):
    schema_version: str = "4.0"
    as_of: datetime
    entity: EntitySummary
    evidence: list[EvidenceLineageRecord]
    crm_actions: list[CRMEvent] = Field(default_factory=list)
    features: list[Feature]
    signals: list[Signal]
    hypotheses: list[Hypothesis]
    ranking: FounderRanking | None = None
    ranking_history: list[FounderRanking] = Field(default_factory=list)
    movement: RankingMovement
    recommendation: Recommendation | None = None
    lineage: list[LineageEdge]
