"""
shared/models.py — complete domain model for the Venture Intelligence OS.

Every concept referenced in the design doc has a corresponding Pydantic
model here.  Nothing downstream invents its own types; everything
imports from this file.

Sections implemented:
  §8   Core domain objects: Observation, Evidence, Signal, Hypothesis,
                            Ranking, Recommendation, Alert
  §12  Canonical event schema (six timestamps)
  §13  All event categories as EventType enum
  §14  Entity types for resolution engine
  §15  Knowledge graph node and edge types
  §18  Nine ranking dimensions and their weights
  §20  CRM stages, valid transitions, partner-action events
  §21  Evaluation result schema
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

# ── Helpers ────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    """Always UTC — never naive datetimes in this codebase."""
    return datetime.now(timezone.utc)


def _uid() -> str:
    return str(uuid4())


def _utc(value: datetime) -> datetime:
    """Normalize an aware timestamp to UTC without inventing a timezone."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include an explicit timezone")
    return value.astimezone(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# §13  Event taxonomy
# ═══════════════════════════════════════════════════════════════════════════════


class EventType(str, Enum):
    # ── Person events ──────────────────────────────────────────────────────────
    FOUNDER_DETECTED = "founder_detected"  # new person seen for first time
    TITLE_CHANGED = "title_changed"  # LinkedIn headline updated
    PROGRAM_MEMBERSHIP_ADDED = "program_membership_added"
    YC_BATCH_ADDED = "yc_batch_added"  # persisted compatibility event

    # ── Company events ─────────────────────────────────────────────────────────
    COMPANY_LISTED = "company_listed"  # appears on YC directory
    COMPANY_WEBSITE_FOUND = "company_website_found"  # domain discovered
    HIRING_PAGE_FOUND = "hiring_page_found"  # /jobs page detected
    FUNDING_ANNOUNCED = "funding_announced"

    # ── Accelerator events ─────────────────────────────────────────────────────
    BATCH_ANNOUNCED = "batch_announced"  # YC announces new batch
    DEMO_DAY = "demo_day"  # demo day detected

    # ── Repository events ──────────────────────────────────────────────────────
    REPO_CREATED = "repo_created"  # new GitHub repo
    COMMIT_VELOCITY_HIGH = "commit_velocity_high"  # >N commits in last 7 days

    # ── Research events ────────────────────────────────────────────────────────
    PAPER_PUBLISHED = "paper_published"  # arXiv or SSRN paper

    # ── Hiring events ──────────────────────────────────────────────────────────
    JOB_POSTED = "job_posted"  # new job listing
    HIRING_VELOCITY_HIGH = "hiring_velocity_high"  # >N open roles in 30 days

    # ── Community events ───────────────────────────────────────────────────────
    HN_MENTIONED = "hn_mentioned"  # HN comment or story mention
    SHOW_HN_POSTED = "show_hn_posted"  # Show HN launch post
    PRODUCT_LAUNCHED = "product_launched"  # PH or HN launch
    SOCIAL_MENTION = "social_mention"  # public social profile/post
    NEWSLETTER_MENTION = "newsletter_mention"  # public RSS/newsletter item
    BLOG_MENTION = "blog_mention"  # public blog post
    COMMUNITY_MEMBERSHIP = "community_membership"  # public community widget signal
    RESEARCH_DISCOVERED = "research_discovered"  # bounded public research result

    # ── CRM events (§20: partner actions become first-class events) ────────────
    PARTNER_INTERESTED = "partner_interested"
    PARTNER_PASSED = "partner_passed"
    MEETING_SCHEDULED = "meeting_scheduled"
    PARTNER_INTRODUCED = "partner_introduced"
    INVESTED = "invested"


class EntityType(str, Enum):
    PERSON = "person"
    COMPANY = "company"
    REPOSITORY = "repository"
    PAPER = "paper"


class Source(str, Enum):
    YC_WEBSITE = "yc_website"
    LINKEDIN = "linkedin"
    GITHUB = "github"
    HACKER_NEWS = "hacker_news"
    ARXIV = "arxiv"
    PRODUCT_HUNT = "product_hunt"
    WELLFOUND = "wellfound"
    DIRECTORY = "directory"
    TRIANGULATION = "triangulation"
    NEWSLETTER = "newsletter"
    BLOG = "blog"
    WEBSITE = "website"
    SOCIAL = "social"
    DISCORD = "discord"
    FIRECRAWL = "firecrawl"
    RESEARCH_AGENT = "research_agent"
    API = "api"
    CRM = "crm"  # partner-generated events


# ═══════════════════════════════════════════════════════════════════════════════
# §12  Canonical event schema
# ═══════════════════════════════════════════════════════════════════════════════


class TemporalPrecision(str, Enum):
    """How precisely a real-world timestamp or validity bound is known."""

    EXACT = "exact"
    DAY = "day"
    MONTH = "month"
    APPROXIMATE = "approximate"
    OBSERVED_FALLBACK = "observed_fallback"
    UNKNOWN = "unknown"


class TemporalStatus(str, Enum):
    """Whether a claim or relationship is current at collection time."""

    CURRENT = "current"
    HISTORICAL = "historical"
    STALE = "stale"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class TemporalValidity(BaseModel):
    """Explicit world-time bounds for a relationship asserted by evidence."""

    valid_from: datetime | None = None
    valid_to: datetime | None = None
    precision: TemporalPrecision = TemporalPrecision.UNKNOWN
    status: TemporalStatus = TemporalStatus.UNKNOWN

    @field_validator("valid_from", "valid_to")
    @classmethod
    def validate_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_interval(self) -> "TemporalValidity":
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("valid_to must not precede valid_from")
        if (
            self.valid_from is None
            and self.valid_to is None
            and self.precision != TemporalPrecision.UNKNOWN
        ):
            raise ValueError("temporal precision requires at least one validity bound")
        return self


class CanonicalEvent(BaseModel):
    """
    The immutable unit of information in the system (§6.1 Event First).

    Once written to the database an event is never modified.  All downstream
    state is derived from events; no state is mutated in place.

    Six timestamps capture the full lifecycle (§6.5 Temporal by Default):
      occurred_at  — when the thing happened in the external world
      observed_at  — when MFC's connector first noticed it
      ingested_at  — when it was written to the database
      processed_at — when the pipeline workers finished with it
      ranked_at    — when the ranking engine updated scores for its entity
      notified_at  — when a partner alert was sent (None until then)
    """

    schema_version: str = "1.1"
    event_id: str = Field(default_factory=_uid)
    event_type: EventType
    entity_type: EntityType
    # Stable cross-source identifier: "<source>__<slug>"
    # e.g. "yc__stripe" or "github__stripe-inc"
    entity_id: str
    source: Source
    source_url: str

    occurred_at: datetime  # world time — required, set by connector
    occurred_at_precision: TemporalPrecision = TemporalPrecision.UNKNOWN
    observed_at: datetime = Field(default_factory=_now)
    ingested_at: datetime = Field(default_factory=_now)
    processed_at: datetime | None = None
    ranked_at: datetime | None = None
    notified_at: datetime | None = None
    validity: TemporalValidity | None = None

    # 0.0–1.0 how sure we are this event is real and correctly parsed
    confidence: float = 0.90
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "occurred_at",
        "observed_at",
        "ingested_at",
        "processed_at",
        "ranked_at",
        "notified_at",
    )
    @classmethod
    def validate_event_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_lifecycle_order(self) -> "CanonicalEvent":
        if self.ingested_at < self.observed_at:
            raise ValueError("ingested_at must not precede observed_at")
        return self


# ═══════════════════════════════════════════════════════════════════════════════
# §17  Evidence chain: Observation → Evidence → Signal → Hypothesis
# ═══════════════════════════════════════════════════════════════════════════════


class Observation(BaseModel):
    """
    A single raw fact extracted from an event (§8, §17).

    The smallest unit of evidence.  Multiple observations combine into
    Evidence; Evidence feeds Signal extraction.

    Example:
      fact  = "Created GitHub repo 'yc-f26-demo' on 2026-07-01"
      value = {"stars": 12, "language": "Python"}
    """

    observation_id: str = Field(default_factory=_uid)
    event_id: str  # FK → CanonicalEvent
    entity_id: str
    source: Source
    fact: str  # human-readable statement
    value: Any = None  # structured numeric/dict value
    observed_at: datetime = Field(default_factory=_now)
    confidence: float = 0.90


class Evidence(BaseModel):
    """
    A supported claim about an entity, backed by observations (§8, §17).

    Example:
      claim            = "Has active GitHub presence"
      observation_ids  = [obs_id_1, obs_id_2]
      confidence       = 0.88
    """

    evidence_id: str = Field(default_factory=_uid)
    entity_id: str
    claim: str
    observation_ids: list[str]
    confidence: float
    observed_at: datetime = Field(default_factory=_now)


class SignalType(str, Enum):
    """
    The seven signal types the Signal Worker extracts (§16).

    Each maps to one or more evidence claims and produces a normalized
    0.0–1.0 value representing signal strength.
    """

    REPEAT_FOUNDER = "repeat_founder"  # has prior company in knowledge graph
    HIRING_VELOCITY = "hiring_velocity"  # open roles / 30-day window
    TECHNICAL_DEPTH = "technical_depth"  # GitHub repos, commit frequency, languages
    RESEARCH_FOUNDER = "research_founder"  # published academic or technical papers
    STEALTH_STARTUP = "stealth_startup"  # LinkedIn updated but no public company yet
    PROGRAM_AFFINITY = "program_affinity"
    # Read compatibility for persisted pre-M6 signals and external consumers.
    # New computation and contracts use PROGRAM_AFFINITY.
    YC_LIKELIHOOD = "yc_likelihood"
    OPEN_SOURCE_LEADER = "open_source_leader"  # GitHub stars, forks, contributors
    HUMAN_FEEDBACK_POSITIVE = "human_feedback_positive"
    HUMAN_FEEDBACK_NEGATIVE = "human_feedback_negative"
    REALIZED_OUTCOME = "realized_outcome"


class Signal(BaseModel):
    """
    Normalized interpretation of one or more evidence items (§8, §16, §17).

    value is always in [0.0, 1.0]:
      0.0 = signal absent
      1.0 = signal at maximum observed strength
    """

    signal_id: str = Field(default_factory=_uid)
    schema_version: str = "4.0"
    signal_version: str = "m4-signal-1"
    entity_id: str
    signal_type: SignalType
    value: float  # 0.0 – 1.0 normalized strength
    evidence_ids: list[str]
    feature_ids: list[str] = Field(default_factory=list)
    crm_action_ids: list[str] = Field(default_factory=list)
    input_digest: str = ""
    explanation: str  # one-sentence human-readable rationale
    confidence: float
    observed_at: datetime = Field(default_factory=_now)


class Hypothesis(BaseModel):
    """
    Probabilistic inference produced by combining signals (§8, §17).

    Example:
      statement  = "Likely building AI Infrastructure tooling"
      probability = 0.78
    """

    hypothesis_id: str = Field(default_factory=_uid)
    schema_version: str = "4.0"
    hypothesis_version: str = "m4-hypothesis-1"
    entity_id: str
    statement: str
    signal_ids: list[str]
    input_digest: str = ""
    probability: float  # 0.0 – 1.0
    created_at: datetime = Field(default_factory=_now)


# ═══════════════════════════════════════════════════════════════════════════════
# §18  Founder Ranking Engine
# ═══════════════════════════════════════════════════════════════════════════════

# The nine dimensions from §18, with their default contribution weights.
# Weights sum to 1.0.  Adjustable via config or partner feedback.
DIMENSION_WEIGHTS: dict[str, float] = {
    "technical_depth": 0.20,  # GitHub, open source, patents
    "research_output": 0.12,  # papers, citations, h-index signals
    "hiring_velocity": 0.12,  # open roles per 30 days
    "network_quality": 0.10,  # connections to known strong founders/investors
    "founder_experience": 0.15,  # prior exits, repeat founder, domain tenure
    "product_momentum": 0.12,  # commits, releases, PH upvotes
    "community_reputation": 0.08,  # HN karma, Twitter/X following, blog reach
    "strategic_fit": 0.06,  # sector alignment with MFC thesis
    "timing": 0.05,  # market tailwinds, regulatory window
}


class RankingDimension(BaseModel):
    """One axis of the nine-dimension ranking (§18)."""

    name: str  # must be one of DIMENSION_WEIGHTS keys
    score: float  # 0.0 – 1.0
    weight: float  # copied from DIMENSION_WEIGHTS (may be partner-tuned)
    evidence: str  # one-sentence explanation of why this score was assigned
    signal_ids: list[str] = Field(default_factory=list)
    feature_ids: list[str] = Field(default_factory=list)


class FounderRanking(BaseModel):
    """
    Complete ranked output for a single entity (§18).

    composite_score = Σ(dimension.score × dimension.weight)
    rank is global across all tracked entities — lower is better.
    """

    ranking_id: str = Field(default_factory=_uid)
    schema_version: str = "4.0"
    ranking_version: str = "m4-ranking-1"
    entity_id: str
    rank: int
    composite_score: float
    dimensions: list[RankingDimension]
    hypothesis_ids: list[str]
    evidence_ids: list[str]
    signal_ids: list[str] = Field(default_factory=list)
    feature_ids: list[str] = Field(default_factory=list)
    crm_action_ids: list[str] = Field(default_factory=list)
    input_digest: str = ""
    reasoning: str  # 2–3 sentence human-readable summary
    confidence: float
    ranked_at: datetime = Field(default_factory=_now)


# ═══════════════════════════════════════════════════════════════════════════════
# §8, §19  Recommendations and Alerts
# ═══════════════════════════════════════════════════════════════════════════════


class RecommendationAction(str, Enum):
    CONTACT = "contact"  # reach out directly
    MONITOR = "monitor"  # keep watching, no action yet
    IGNORE = "ignore"  # below threshold — skip
    ESCALATE = "escalate"  # bring to weekly partner meeting
    INTRODUCE_PARTNER = "introduce_partner"  # make warm intro


class Recommendation(BaseModel):
    """
    Suggested partner action, backed by evidence and ranking (§8, §16).

    Recommendations are generated by the Recommendation Worker for every
    entity that crosses the ranking threshold.  They only become Alerts
    if they also clear the confidence threshold in the Notification Pipeline.
    """

    recommendation_id: str = Field(default_factory=_uid)
    schema_version: str = "5.0"
    recommendation_version: str = "m5-recommendation-1"
    entity_id: str
    action: RecommendationAction
    reasoning: str
    evidence_ids: list[str]
    ranking_id: str | None = None
    signal_ids: list[str] = Field(default_factory=list)
    feature_ids: list[str] = Field(default_factory=list)
    crm_action_ids: list[str] = Field(default_factory=list)
    input_digest: str = ""
    supersedes_recommendation_id: str | None = None
    confidence: float
    created_at: datetime = Field(default_factory=_now)


class Alert(BaseModel):
    """
    A recommendation that cleared all thresholds and was dispatched to
    partners (§8, §19).

    The gap between Recommendation.created_at and Alert.fired_at measures
    pipeline latency.  notified_at is set when the Slack/email sink confirms
    delivery.
    """

    alert_id: str = Field(default_factory=_uid)
    schema_version: str = "5.0"
    alert_version: str = "m5-alert-1"
    recommendation_id: str
    entity_id: str
    entity_name: str
    action: RecommendationAction
    reasoning: str
    evidence_summary: list[str]  # 3–5 bullet points for the partner briefing
    rank: int | None = None
    confidence: float
    source_urls: list[str]
    crm_action_ids: list[str] = Field(default_factory=list)
    fired_at: datetime = Field(default_factory=_now)
    notified_at: datetime | None = None


class RecommendationLifecycleStatus(str, Enum):
    CREATED = "created"
    SUPERSEDED = "superseded"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    COMPLETED = "completed"


class AlertLifecycleStatus(str, Enum):
    CREATED = "created"
    DISPATCHING = "dispatching"
    DELIVERED = "delivered"
    PARTIAL = "partial"
    FAILED = "failed"
    ACKNOWLEDGED = "acknowledged"
    DISMISSED = "dismissed"
    RESOLVED = "resolved"


class RecommendationLifecycleEvent(BaseModel):
    lifecycle_event_id: str = Field(default_factory=_uid)
    recommendation_id: str
    entity_id: str
    status: RecommendationLifecycleStatus
    actor: str
    reason: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=_now)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return _utc(value)


class AlertLifecycleEvent(BaseModel):
    lifecycle_event_id: str = Field(default_factory=_uid)
    alert_id: str
    recommendation_id: str
    entity_id: str
    status: AlertLifecycleStatus
    actor: str
    reason: str | None = None
    delivery: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=_now)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return _utc(value)


# ═══════════════════════════════════════════════════════════════════════════════
# §14  Entity models
# ═══════════════════════════════════════════════════════════════════════════════


class Person(BaseModel):
    """
    A deduplicated, canonical representation of a founder (§14).

    A single real person may arrive via LinkedIn (linkedin__john-doe),
    GitHub (github__johndoe), and YC page (yc__acme).  The Entity
    Resolution Engine merges these into one Person with all three
    source IDs in properties["source_ids"].
    """

    person_id: str = Field(default_factory=_uid)
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    linkedin_url: str | None = None
    github_username: str | None = None
    twitter_handle: str | None = None
    current_title: str | None = None
    current_company_id: str | None = None
    batch: str | None = None
    identity_confidence: float = 0.90
    first_seen_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    properties: dict[str, Any] = Field(default_factory=dict)


class Company(BaseModel):
    """
    A deduplicated, canonical representation of a startup (§14).
    """

    company_id: str = Field(default_factory=_uid)
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    slug: str | None = None
    yc_url: str | None = None
    website: str | None = None
    description: str | None = None
    batch: str | None = None
    tags: list[str] = Field(default_factory=list)
    founder_ids: list[str] = Field(default_factory=list)
    identity_confidence: float = 0.90
    first_seen_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    properties: dict[str, Any] = Field(default_factory=dict)


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
# §21  Evaluation framework
# ═══════════════════════════════════════════════════════════════════════════════


class EvaluationResult(BaseModel):
    """
    Post-batch evaluation metrics (§21).

    Computed after each YC batch is fully announced (Demo Day + n weeks).
    Used to tune weights in DIMENSION_WEIGHTS and alert thresholds.
    """

    batch: str
    evaluated_at: datetime = Field(default_factory=_now)

    # Ranking quality
    precision_at_10: float  # % of top-10 alerts that were real YC companies
    precision_at_25: float  # same for top-25
    recall: float  # % of actual YC companies MFC detected early

    # Lead time (the core competitive advantage)
    avg_lead_time_days: float  # days before official YC announcement
    median_lead_time_days: float

    # Error analysis
    false_positives: int  # alerts on non-YC companies
    missed_companies: int  # YC companies we never detected

    # Business metrics
    alert_acceptance_rate: float  # % of CONTACT alerts acted on by partner
    investment_conversion_rate: float  # % of CONTACT alerts → investment

    total_entities_ranked: int
    notes: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# §8  Feature — raw measurable value used by ranking (distinct from Signal)
# ═══════════════════════════════════════════════════════════════════════════════


class Feature(BaseModel):
    """
    A measurable raw value extracted from observations (§8).

    Features are the lowest-level numeric inputs to the ranking engine.
    They differ from Signals:
      Feature  = raw measurement  (e.g. github_stars = 142)
      Signal   = normalized 0–1 interpretation  (technical_depth = 0.63)

    Examples from §8:
      github_stars, research_papers, hiring_velocity,
      founder_count, community_centrality
    """

    feature_id: str = Field(default_factory=_uid)
    schema_version: str = "4.0"
    feature_version: str = "m4-feature-1"
    entity_id: str
    name: str  # e.g. "github_stars"
    value: float  # raw numeric value
    unit: str | None = None  # e.g. "count", "days", "USD"
    source: Source
    observed_at: datetime = Field(default_factory=_now)
    as_of: datetime | None = None
    computed_at: datetime | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    crm_action_ids: list[str] = Field(default_factory=list)
    formula: str = "legacy_direct_value"
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_digest: str = ""
    program_id: str | None = None
    batch_id: str | None = None
    confidence: float = 0.90

    @field_validator("observed_at", "as_of", "computed_at")
    @classmethod
    def validate_feature_timestamp(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def normalize_feature_times(self) -> "Feature":
        if self.as_of is None:
            self.as_of = self.observed_at
        if self.computed_at is None:
            self.computed_at = self.as_of
        return self


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


# ═══════════════════════════════════════════════════════════════════════════════
# §16  Classification Worker output — person type labels
# ═══════════════════════════════════════════════════════════════════════════════


class PersonType(str, Enum):
    """
    Classification Worker output (§16).
    Determines how the ranking engine weights signals for this person.
    """

    FOUNDER = "founder"
    INVESTOR = "investor"
    RESEARCHER = "researcher"
    OPERATOR = "operator"
    UNKNOWN = "unknown"


class PersonClassification(BaseModel):
    """Result of the Classification Worker for one person entity."""

    classification_id: str = Field(default_factory=_uid)
    entity_id: str
    person_type: PersonType
    confidence: float
    evidence_ids: list[str] = Field(default_factory=list)
    classified_at: datetime = Field(default_factory=_now)


# ═══════════════════════════════════════════════════════════════════════════════
# Bounded public research contracts
# ═══════════════════════════════════════════════════════════════════════════════


class ResearchTargetType(str, Enum):
    COMPANY = "company"
    PERSON = "person"
    PRODUCT = "product"
    PROGRAM = "program"
    OTHER = "other"


class ResearchSourceStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    SEARCH_DISCOVERABLE = "SEARCH_DISCOVERABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    BLOCKED = "BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class SourceAccessMode(str, Enum):
    """Allowed acquisition path for sources that commonly require sign-in."""

    SEARCH_DISCOVERY = "search_discovery"
    APPROVED_API = "approved_api"
    MANUAL_IMPORT = "manual_import"
    APPROVED_PUBLIC_WEB = "approved_public_web"
    PERMISSION_REQUIRED = "permission_required"
    UNSUPPORTED = "unsupported"


class SourceOperationStatus(str, Enum):
    """Normalized outcome vocabulary shared by every acquisition boundary."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    EMPTY = "EMPTY"
    SKIPPED = "SKIPPED"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    BLOCKED = "BLOCKED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    PARSER_ERROR = "PARSER_ERROR"
    UNSUPPORTED = "UNSUPPORTED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FAILED = "FAILED"


class ResearchDiscoveryMethod(str, Enum):
    DIRECT_PUBLIC = "DIRECT_PUBLIC"
    SEARCH_ENGINE = "SEARCH_ENGINE"
    RSS = "RSS"
    PUBLIC_API = "PUBLIC_API"


class EvidenceAttributionStatus(str, Enum):
    """Whether a research result is safe to attach to a canonical identity."""

    ATTACHED = "attached"
    UNBOUND = "unbound"
    CONTRADICTORY = "contradictory"


class ResearchIdentityStatus(str, Enum):
    VERIFIED = "verified"
    CORROBORATED = "corroborated"
    AMBIGUOUS = "ambiguous"
    PROVISIONAL = "provisional"
    UNRESOLVED = "unresolved"


class ResearchProgramStatus(str, Enum):
    VERIFIED = "verified"
    SUPPORTED = "supported"
    HYPOTHESIS = "hypothesis"
    UNKNOWN = "unknown"
    CONTRADICTED = "contradicted"


class ResearchQueryIntent(str, Enum):
    """Provider-neutral execution intent for capability routing."""

    WEB_DISCOVERY = "web_discovery"
    PUBLICATION = "publication"
    TECHNICAL = "technical"
    HISTORICAL = "historical"


class ResearchBudget(BaseModel):
    max_queries: int = Field(default=20, ge=1, le=500)
    max_depth: int = Field(default=3, ge=0, le=10)
    max_results_per_query: int = Field(default=5, ge=1, le=50)
    max_runtime_seconds: float = Field(default=30.0, gt=0, le=300)
    max_total_requests: int = Field(default=30, ge=1, le=1_000)
    max_requests_per_provider: int = Field(default=12, ge=1, le=500)
    max_retry_requests: int = Field(default=8, ge=0, le=500)
    max_requests_per_domain: int = Field(default=20, ge=1, le=500)


class ResearchTarget(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: ResearchTargetType = ResearchTargetType.OTHER
    program_id: str | None = None
    aliases: list[str] = Field(default_factory=list, max_length=20)
    domains: list[str] = Field(default_factory=list, max_length=20)


class ResearchQuery(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    family: str = "exact"
    depth: int = Field(default=0, ge=0)
    discovery_method: ResearchDiscoveryMethod = ResearchDiscoveryMethod.SEARCH_ENGINE
    intent: ResearchQueryIntent = ResearchQueryIntent.WEB_DISCOVERY


class ResearchProviderSkip(BaseModel):
    provider_id: str
    query: str
    intent: ResearchQueryIntent
    reason: str
    request_count: int = 0


class ResearchRejectedResult(BaseModel):
    url: str
    provider_domain: str
    query: str
    reason: str


class ResearchSource(BaseModel):
    domain: str
    status: ResearchSourceStatus
    access_mode: str = "search_discovery"
    source_tier: int = Field(default=3, ge=1, le=4)
    http_status: int | None = None
    queries_attempted: int = 0
    retries_used: int = 0
    notes: str | None = None
    normalized_status: SourceOperationStatus


class WebsiteLifecycleSignal(str, Enum):
    """Public website changes that may be asserted by archived evidence."""

    WEBSITE_APPEARED = "website_appeared"
    TEAM_PAGE_OBSERVED = "team_page_observed"
    FOUNDER_PAGE_OBSERVED = "founder_page_observed"
    CAREERS_PAGE_OBSERVED = "careers_page_observed"
    DOCUMENTATION_OBSERVED = "documentation_observed"
    PRICING_PAGE_OBSERVED = "pricing_page_observed"
    CHANGELOG_OBSERVED = "changelog_observed"


class HistoricalWebObservation(BaseModel):
    """Typed temporal/provenance contract for one public archive snapshot."""

    original_url: str
    archive_url: str
    snapshot_at: datetime
    retrieved_at: datetime
    temporal_status: TemporalStatus = TemporalStatus.HISTORICAL
    precision: TemporalPrecision = TemporalPrecision.EXACT
    lifecycle_signals: list[WebsiteLifecycleSignal] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("snapshot_at", "retrieved_at")
    @classmethod
    def validate_historical_timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class ResearchEvidence(BaseModel):
    evidence_id: str = Field(default_factory=_uid)
    url: str
    canonical_url: str | None = None
    title: str = ""
    snippet: str = ""
    source_domain: str
    retrieved_at: datetime = Field(default_factory=_now)
    published_at: datetime | None = None
    discovery_method: ResearchDiscoveryMethod = ResearchDiscoveryMethod.SEARCH_ENGINE
    query: str
    confidence: float = Field(ge=0, le=1)
    confidence_label: str = "discovered"
    target_name: str
    entity_type: ResearchTargetType = ResearchTargetType.OTHER
    direct_public: bool = False
    source_status: ResearchSourceStatus = ResearchSourceStatus.SEARCH_DISCOVERABLE
    temporal_status: str = "current"
    provenance: dict[str, Any] = Field(default_factory=dict)
    depth: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    attribution_status: EvidenceAttributionStatus = EvidenceAttributionStatus.UNBOUND
    attribution_reason: str = "identity_not_anchored"
    evidence_origin_url: str | None = None
    acquisition_providers: list[str] = Field(default_factory=list)


class ResearchEntity(BaseModel):
    name: str
    entity_type: ResearchTargetType = ResearchTargetType.OTHER
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)


class ResearchHypothesis(BaseModel):
    statement: str
    status: str = "discovered"
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class ResearchRun(BaseModel):
    run_id: str = Field(default_factory=_uid)
    target: ResearchTarget
    queries: list[ResearchQuery] = Field(default_factory=list)
    sources: list[ResearchSource] = Field(default_factory=list)
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    entities: list[ResearchEntity] = Field(default_factory=list)
    hypotheses: list[ResearchHypothesis] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    confidence_label: str = "discovered"
    next_actions: list[str] = Field(default_factory=list)
    budget: ResearchBudget
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    stop_reason: str = "budget_exhausted"
    events_emitted: int = 0
    queries_succeeded: int = 0
    queries_failed: int = 0
    sources_checked: int = 0
    followups_executed: int = 0
    retries_used: int = 0
    budget_remaining: int = 0
    requests_used: int = 0
    retry_requests_used: int = 0
    redirect_requests_used: int = 0
    request_budget_remaining: int = 0
    provider_request_counts: dict[str, int] = Field(default_factory=dict)
    domain_request_counts: dict[str, int] = Field(default_factory=dict)
    provider_skips: list[ResearchProviderSkip] = Field(default_factory=list)
    rejected_results: list[ResearchRejectedResult] = Field(default_factory=list)
    retrieved_results: int = 0
    relevant_results: int = 0
    identity_status: ResearchIdentityStatus = ResearchIdentityStatus.UNRESOLVED
    program_status: ResearchProgramStatus = ResearchProgramStatus.UNKNOWN
    independent_evidence_origins: int = Field(default=0, ge=0)
    attached_evidence_count: int = Field(default=0, ge=0)
    unbound_evidence_count: int = Field(default=0, ge=0)
    contradictory_evidence_count: int = Field(default=0, ge=0)


# ═══════════════════════════════════════════════════════════════════════════════
# a16z Speedrun — additional event and signal types
# ═══════════════════════════════════════════════════════════════════════════════
# These extend the existing taxonomy; no existing code changes.

# Added to EventType enum — done as module-level patches so the enum stays intact
# Real usage: import SpeedrunEventType and treat identically to EventType


class SpeedrunEventType(str):
    """
    Speedrun-specific event type strings.  Written as plain strings (not extending
    the EventType Enum) so the event bus and DB accept them without schema changes.
    Stored in events.event_type alongside regular EventType values.
    """

    SPEEDRUN_LISTED = "speedrun_listed"  # company confirmed in SR batch
    SPEEDRUN_BATCH_ADDED = "speedrun_batch_added"  # founder added SR batch to profile


class AcceleratorProgram(str):
    """Deprecated persisted identifiers; ProgramRegistry owns current ids."""

    YC_F26 = "yc_f26"
    SPEEDRUN_SR007 = "speedrun_sr007"
