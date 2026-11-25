"""CRM workflow (§20) and human-led investigations (§8)."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from venture_intelligence.shared.models._helpers import _now, _uid, _utc

# Classes and helpers defined here keep the historical public module path.
# OpenAPI component names (e.g. venture_intelligence__shared__models__SourceAccessMode
# on a name collision), pydantic core-schema refs and pickled qualified paths all
# derive from ``__module__``, which Python takes from ``__name__`` at definition time.
__name__ = "venture_intelligence.shared.models"

# ═══════════════════════════════════════════════════════════════════════════════
# §20  CRM workflow
# ═══════════════════════════════════════════════════════════════════════════════


class CRMStage(str, Enum):
    """
    Eight stages of the partner workflow (§20).

    Transitions are constrained by CRM_TRANSITIONS below.
    Every stage change is recorded as a CRMEvent so the full
    history is auditable.
    """

    DISCOVERY = "discovery"  # system auto-creates on first detection
    QUALIFICATION = "qualification"  # analyst confirms signal quality
    PARTNER_REVIEW = "partner_review"  # weekly partner meeting agenda item
    INVESTIGATION = "investigation"  # deep diligence phase
    RELATIONSHIP = "relationship"  # active founder engagement
    INVESTMENT = "investment"  # term sheet / closing
    PORTFOLIO = "portfolio"  # invested
    PASSED = "passed"  # explicitly decided not to pursue


# Valid forward and backward transitions.
# PASSED → DISCOVERY allows re-entry if signals materially change.
CRM_TRANSITIONS: dict[CRMStage, list[CRMStage]] = {
    CRMStage.DISCOVERY: [CRMStage.QUALIFICATION, CRMStage.PASSED],
    CRMStage.QUALIFICATION: [CRMStage.PARTNER_REVIEW, CRMStage.PASSED],
    CRMStage.PARTNER_REVIEW: [CRMStage.INVESTIGATION, CRMStage.PASSED],
    CRMStage.INVESTIGATION: [CRMStage.RELATIONSHIP, CRMStage.PASSED],
    CRMStage.RELATIONSHIP: [CRMStage.INVESTMENT, CRMStage.PASSED],
    CRMStage.INVESTMENT: [CRMStage.PORTFOLIO],
    CRMStage.PORTFOLIO: [],
    CRMStage.PASSED: [CRMStage.DISCOVERY],
}


class CRMRecord(BaseModel):
    """Current state of a founder/company in the partner workflow (§20)."""

    crm_id: str = Field(default_factory=_uid)
    entity_id: str
    stage: CRMStage = CRMStage.DISCOVERY
    notes: list[str] = Field(default_factory=list)  # analyst annotations
    decision: str | None = None  # free-text if PASSED
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CRMActionType(str, Enum):
    STAGE_CHANGED = "stage_changed"
    NOTE_ADDED = "note_added"
    MEETING_SCHEDULED = "meeting_scheduled"
    PASSED = "passed"
    INTERESTED = "interested"
    INTRODUCED = "introduced"
    INVESTED = "invested"
    WATCHLISTED = "watchlisted"
    FOLLOW_UP_REQUESTED = "follow_up_requested"
    RELATIONSHIP_STARTED = "relationship_started"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    DUPLICATE = "duplicate"
    IDENTITY_CORRECTED = "identity_corrected"
    PRIORITY_CHANGED = "priority_changed"
    INVESTIGATION_REQUESTED = "investigation_requested"
    OUTREACH = "outreach"
    DILIGENCE = "diligence"


class CRMFeedbackKind(str, Enum):
    WORKFLOW = "workflow"
    HUMAN_JUDGMENT = "human_judgment"
    REALIZED_EXTERNAL_OUTCOME = "realized_external_outcome"


class CRMEvent(BaseModel):
    """
    Immutable record of a single partner action (§20).

    Partner actions are themselves events — they flow back through the
    event bus so rankings and signals can incorporate human feedback.
    """

    crm_event_id: str = Field(default_factory=_uid)
    schema_version: str = "5.0"
    action_version: str = "m5-crm-action-1"
    entity_id: str
    action: CRMActionType
    feedback_kind: CRMFeedbackKind
    from_stage: CRMStage
    to_stage: CRMStage
    note: str | None = None
    partner: str
    ranking_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=_now)
    observed_at: datetime = Field(default_factory=_now)
    created_at: datetime = Field(default_factory=_now)
    input_digest: str = ""

    @field_validator("occurred_at", "observed_at", "created_at")
    @classmethod
    def validate_crm_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


# ═══════════════════════════════════════════════════════════════════════════════
# §8  Investigation — human-led deep review of a founder (§8, §20)
# ═══════════════════════════════════════════════════════════════════════════════


class InvestigationStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CONCLUDED = "concluded"
    ABANDONED = "abandoned"


class Investigation(BaseModel):
    """
    A structured human review of a founder or company (§8).

    Created when a partner moves a CRM record to INVESTIGATION stage.
    Contains: notes, evidence links, conversations, and a decision.

    Distinct from CRMRecord (which tracks stage) — an Investigation
    captures the qualitative reasoning that led to a decision.
    """

    investigation_id: str = Field(default_factory=_uid)
    entity_id: str
    status: InvestigationStatus = InvestigationStatus.OPEN
    lead_partner: str | None = None
    # Structured notes: each is {"timestamp": ..., "author": ..., "text": ...}
    notes: list[dict[str, Any]] = Field(default_factory=list)
    # Links to evidence records that informed the review
    evidence_ids: list[str] = Field(default_factory=list)
    # Key questions the team is trying to answer
    open_questions: list[str] = Field(default_factory=list)
    # Final conclusion: "strong_pass", "soft_pass", "pass", "invest"
    decision: str | None = None
    decision_reasoning: str | None = None
    opened_at: datetime = Field(default_factory=_now)
    concluded_at: datetime | None = None
    updated_at: datetime = Field(default_factory=_now)
