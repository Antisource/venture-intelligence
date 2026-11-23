"""Bounded public research contracts."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from venture_intelligence.shared.models._helpers import _now, _uid, _utc
from venture_intelligence.shared.models.events import TemporalPrecision, TemporalStatus

# Classes and helpers defined here keep the historical public module path.
# OpenAPI component names (e.g. venture_intelligence__shared__models__SourceAccessMode
# on a name collision), pydantic core-schema refs and pickled qualified paths all
# derive from ``__module__``, which Python takes from ``__name__`` at definition time.
__name__ = "venture_intelligence.shared.models"

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
