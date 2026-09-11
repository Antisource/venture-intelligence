"""Authoritative JSON contracts for isolated historical replay."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from venture_intelligence.shared.models import Source


class ReplayMode(str, Enum):
    """The timestamp axis used to select immutable events."""

    OBSERVED_AT = "observed_at"
    OCCURRED_AT = "occurred_at"


class ReplayStatus(str, Enum):
    COMPLETED = "completed"
    EMPTY = "empty"
    PARTIAL = "partial"
    FAILED = "failed"


class ReplayRequest(BaseModel):
    """Bounded replay request. Historical knowledge defaults to observation time."""

    cutoff: datetime
    mode: ReplayMode = ReplayMode.OBSERVED_AT
    start_at: datetime | None = None
    max_events: int = Field(default=5_000, ge=1, le=25_000)
    excluded_sources: list[Source] = Field(default_factory=list, max_length=16)

    @field_validator("cutoff", "start_at")
    @classmethod
    def require_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("replay timestamps must include a UTC offset")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_range(self) -> "ReplayRequest":
        if self.start_at is not None and self.start_at > self.cutoff:
            raise ValueError("start_at must not follow cutoff")
        if len(set(self.excluded_sources)) != len(self.excluded_sources):
            raise ValueError("excluded_sources must not contain duplicates")
        return self


class ReplayError(BaseModel):
    event_id: str | None = None
    stage: str
    code: str
    message: str


class ReplayProjectionCounts(BaseModel):
    eligible_events: int = 0
    replayed_events: int = 0
    rejected_events: int = 0
    eligible_crm_actions: int = 0
    replayed_crm_actions: int = 0
    companies: int = 0
    persons: int = 0
    evidence: int = 0
    features: int = 0
    signals: int = 0
    hypotheses: int = 0
    rankings: int = 0
    recommendations: int = 0
    graph_nodes: int = 0
    graph_edges: int = 0
    alerts: int = 0
    crm_records: int = 0


class ReplaySnapshot(BaseModel):
    """Stable semantic projection; volatile generated identifiers are excluded."""

    entities: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    crm_actions: list[dict[str, Any]] = Field(default_factory=list)
    features: list[dict[str, Any]] = Field(default_factory=list)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    rankings: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    graph_nodes: list[dict[str, Any]] = Field(default_factory=list)
    graph_edges: list[dict[str, Any]] = Field(default_factory=list)


class ReplayResult(BaseModel):
    schema_version: str = "2.0"
    engine_version: str = "m2-replay-1"
    replay_id: str
    status: ReplayStatus
    cutoff: datetime
    mode: ReplayMode
    start_at: datetime | None = None
    excluded_sources: list[Source] = Field(default_factory=list)
    default_historical_field: str = "observed_at"
    source_database: str
    isolated_store: str = "temporary_sqlite"
    isolated_store_disposed: bool = True
    live_state_mutated: bool = False
    side_effects_suppressed: list[str]
    selected_event_ids: list[str]
    selected_crm_action_ids: list[str] = Field(default_factory=list)
    input_digest: str
    projection_digest: str
    counts: ReplayProjectionCounts
    errors: list[ReplayError] = Field(default_factory=list)
    snapshot: ReplaySnapshot
    created_at: datetime
    schema_metadata: dict[str, str]


class ReplayContractResponse(BaseModel):
    schema_version: str = "2.0"
    engine_version: str = "m2-replay-1"
    default_mode: ReplayMode = ReplayMode.OBSERVED_AT
    supported_modes: list[ReplayMode] = Field(
        default_factory=lambda: [ReplayMode.OBSERVED_AT, ReplayMode.OCCURRED_AT]
    )
    isolated_store: str = "temporary_sqlite"
    remote_execution_enabled: bool = False
    cli_command: str
    max_events: int = 25_000
    side_effects_suppressed: list[str]
