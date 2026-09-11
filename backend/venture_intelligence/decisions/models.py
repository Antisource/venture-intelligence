"""Authoritative typed contracts for the M5 decision workspace."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from venture_intelligence.intelligence.models import EntityIntelligenceResponse, EntitySummary
from venture_intelligence.shared.models import (
    Alert,
    AlertLifecycleEvent,
    CRMEvent,
    CRMRecord,
    CRMStage,
    Feature,
    FounderRanking,
    Recommendation,
    RecommendationLifecycleEvent,
    Signal,
)


class DecisionQueueItem(BaseModel):
    entity: EntitySummary
    crm: CRMRecord
    allowed_transitions: list[CRMStage]
    action_count: int
    feedback_features: list[Feature] = Field(default_factory=list)
    ranking: FounderRanking | None = None
    recommendation: Recommendation | None = None
    recommendation_status: str | None = None
    latest_alert: Alert | None = None
    alert_status: str | None = None


class DecisionBoardResponse(BaseModel):
    schema_version: str = "5.0"
    as_of: datetime
    stages: dict[CRMStage, list[DecisionQueueItem]]


class RecommendationTimeline(BaseModel):
    recommendation: Recommendation
    lifecycle: list[RecommendationLifecycleEvent] = Field(default_factory=list)


class AlertTimeline(BaseModel):
    alert: Alert
    lifecycle: list[AlertLifecycleEvent] = Field(default_factory=list)


class DecisionEntityResponse(BaseModel):
    schema_version: str = "5.0"
    as_of: datetime
    intelligence: EntityIntelligenceResponse
    crm: CRMRecord
    allowed_transitions: list[CRMStage]
    actions: list[CRMEvent]
    feedback_features: list[Feature]
    feedback_signals: list[Signal]
    recommendations: list[RecommendationTimeline]
    alerts: list[AlertTimeline]


class CRMActionResult(BaseModel):
    action: CRMEvent
    decision: DecisionEntityResponse
