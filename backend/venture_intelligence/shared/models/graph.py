"""Temporal knowledge graph node and edge types (§15)."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from venture_intelligence.shared.models._helpers import _now, _uid, _utc
from venture_intelligence.shared.models.events import (
    TemporalPrecision,
    TemporalStatus,
    TemporalValidity,
)

# Classes and helpers defined here keep the historical public module path.
# OpenAPI component names (e.g. venture_intelligence__shared__models__SourceAccessMode
# on a name collision), pydantic core-schema refs and pickled qualified paths all
# derive from ``__module__``, which Python takes from ``__name__`` at definition time.
__name__ = "venture_intelligence.shared.models"

# ═══════════════════════════════════════════════════════════════════════════════
# §15  Knowledge Graph types
# ═══════════════════════════════════════════════════════════════════════════════


class GraphNodeType(str, Enum):
    """
    Node types in the temporal knowledge graph (§15).

    The graph is implemented as SQLite adjacency tables in V1.
    Design is intentionally isomorphic to Neo4j so the V2 migration
    is a data export + import, not a schema redesign.
    """

    PERSON = "Person"
    COMPANY = "Company"
    ORGANIZATION = "Organization"
    UNIVERSITY = "University"
    EMPLOYER = "Employer"
    ACCELERATOR = "Accelerator"
    COMMUNITY = "Community"
    REPOSITORY = "Repository"
    RESEARCH_PAPER = "ResearchPaper"
    JOB_POSTING = "JobPosting"
    GRANT = "Grant"


class GraphEdgeType(str, Enum):
    """
    Relationship types between graph nodes (§15).
    Follows Neo4j convention: VERB in SCREAMING_SNAKE_CASE.
    """

    FOUNDED = "FOUNDED"
    WORKED_AT = "WORKED_AT"
    MEMBER_OF = "MEMBER_OF"
    CREATED = "CREATED"  # person → repository
    AUTHORED = "AUTHORED"  # person → research paper
    HIRED = "HIRED"  # company → person
    RECEIVED = "RECEIVED"  # person/company → grant
    COLLABORATED = "COLLABORATED_WITH"
    INVESTED_BY = "INVESTED_BY"
    INVESTED_IN = "INVESTED_IN"
    LED_INVESTMENT_IN = "LED_INVESTMENT_IN"
    WORKS_AT = "WORKS_AT"
    APPLIED_TO = "APPLIED_TO"
    ACCEPTED_BY = "ACCEPTED_BY"
    REJECTED_BY = "REJECTED_BY"
    MENTIONED_IN = "MENTIONED_IN"


class GraphNode(BaseModel):
    node_id: str = Field(default_factory=_uid)
    node_type: GraphNodeType
    label: str  # display name
    properties: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class GraphEdge(BaseModel):
    edge_id: str = Field(default_factory=_uid)
    source_id: str
    target_id: str
    edge_type: GraphEdgeType
    confidence: float = 0.90
    observed_at: datetime = Field(default_factory=_now)
    valid_from: datetime | None = None  # when relationship started
    valid_to: datetime | None = None  # when it ended (None = still active)
    validity_precision: TemporalPrecision = TemporalPrecision.UNKNOWN
    temporal_status: TemporalStatus = TemporalStatus.UNKNOWN
    evidence_ids: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at", "valid_from", "valid_to")
    @classmethod
    def validate_graph_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_validity(self) -> "GraphEdge":
        TemporalValidity(
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            precision=self.validity_precision,
            status=self.temporal_status,
        )
        return self
