"""Authoritative JSON contracts for the bounded temporal graph workspace."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from venture_intelligence.shared.models import (
    GraphEdgeType,
    GraphNodeType,
    TemporalPrecision,
    TemporalStatus,
    TemporalValidity,
)


class GraphProjectionSource(str, Enum):
    LIVE = "live"
    ISOLATED_REPLAY = "isolated_replay"


class GraphProvenance(BaseModel):
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    source_urls: list[str] = Field(default_factory=list, max_length=32)


class TemporalGraphNode(BaseModel):
    node_id: str
    node_type: GraphNodeType
    label: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    program_id: str | None = None
    first_observed_at: datetime | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    provenance: GraphProvenance = Field(default_factory=GraphProvenance)


class TemporalGraphEdge(BaseModel):
    edge_id: str
    source_id: str
    target_id: str
    edge_type: GraphEdgeType
    confidence: float = Field(ge=0, le=1)
    observed_at: datetime
    validity: TemporalValidity
    temporal_status: TemporalStatus
    validity_precision: TemporalPrecision
    properties: dict[str, Any] = Field(default_factory=dict)
    provenance: GraphProvenance = Field(default_factory=GraphProvenance)


class GraphSnapshotLimits(BaseModel):
    max_nodes: int = Field(ge=1, le=400)
    max_edges: int = Field(ge=1, le=800)
    max_depth: int = Field(ge=0, le=2)
    max_replay_events: int = Field(ge=1, le=5_000)


class GraphSnapshotResponse(BaseModel):
    schema_version: str = "7.0"
    as_of: datetime
    historical_cutoff_field: str = "observed_at"
    projection_source: GraphProjectionSource
    program_id: str | None = None
    focus_id: str | None = None
    depth: int = Field(ge=0, le=2)
    nodes: list[TemporalGraphNode]
    edges: list[TemporalGraphEdge]
    total_nodes: int = 0
    total_edges: int = 0
    truncated: bool = False
    limits: GraphSnapshotLimits
    available_node_types: list[GraphNodeType] = Field(default_factory=list)
    available_edge_types: list[GraphEdgeType] = Field(default_factory=list)
    replay_projection_digest: str | None = None
    semantics: str = (
        "Historical snapshots are rebuilt in an isolated store from immutable events "
        "where observed_at <= as_of; validity is never inferred from observation time."
    )
