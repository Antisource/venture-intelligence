"""Typed JSON contracts for discovery before canonical entity promotion."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from venture_intelligence.shared.models import ResearchBudget, SourceOperationStatus


def _id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include an explicit timezone")
    return value.astimezone(timezone.utc)


class SourceTaxonomyRole(str, Enum):
    DISCOVERY = "DISCOVERY"
    IDENTITY = "IDENTITY"
    VERIFICATION = "VERIFICATION"
    ENRICHMENT = "ENRICHMENT"
    HISTORICAL = "HISTORICAL"
    FUNDING = "FUNDING"
    INVESTOR_SIGNAL = "INVESTOR_SIGNAL"
    MARKET_SIGNAL = "MARKET_SIGNAL"
    TEMPORAL_NEWS = "TEMPORAL_NEWS"


class SourceAccessMode(str, Enum):
    PUBLIC_SEARCH = "public_search"
    PUBLIC_DIRECT = "public_direct"
    INDEXED_PUBLIC_REFERENCE = "indexed_public_reference"
    MANUAL_OFFLINE_IMPORT = "manual_offline_import"


class ObservationStance(str, Enum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    OTHER_BATCH = "other_batch"
    AMBIGUOUS_IDENTITY = "ambiguous_identity"
    STALE = "stale"
    NEUTRAL = "neutral"


class SourceIndependence(str, Enum):
    INDEPENDENT = "independent"
    DERIVATIVE = "derivative"
    UNKNOWN = "unknown"


class CandidateKind(str, Enum):
    PERSON = "person"
    COMPANY = "company"


class CandidateState(str, Enum):
    DISCOVERED = "discovered"
    SUSPECTED = "suspected"
    CORROBORATED = "corroborated"
    HIGH_CONFIDENCE = "high_confidence"
    CONFIRMED = "confirmed"
    UNCERTAIN = "uncertain"
    REJECTED = "rejected"


class DiscoveryStage(str, Enum):
    DISCOVERY = "discovery"
    VERIFICATION = "verification"
    ENRICHMENT = "enrichment"
    COMPLETED = "completed"


class DiscoveryDropReason(str, Enum):
    IRRELEVANT_TO_VENTURE = "IRRELEVANT_TO_VENTURE"
    NOT_VENTURE_EVENT = "NOT_VENTURE_EVENT"
    NO_CANDIDATE_CLAIM = "NO_CANDIDATE_CLAIM"
    NO_ENTITY_MENTION = "NO_ENTITY_MENTION"
    ENTITY_ROLE_AMBIGUOUS = "ENTITY_ROLE_AMBIGUOUS"
    INSUFFICIENT_CONTENT = "INSUFFICIENT_CONTENT"
    RESTRICTED_DESTINATION_NOT_FETCHED = "RESTRICTED_DESTINATION_NOT_FETCHED"
    KNOWN_EXISTING_ENTITY = "KNOWN_EXISTING_ENTITY"
    OUTSIDE_TIME_WINDOW = "OUTSIDE_TIME_WINDOW"
    NO_EXTRACTABLE_ENTITY = "NO_EXTRACTABLE_ENTITY"
    INSUFFICIENT_IDENTITY = "INSUFFICIENT_IDENTITY"
    DERIVATIVE_DUPLICATE = "DERIVATIVE_DUPLICATE"
    INSUFFICIENT_INDEPENDENT_ORIGINS = "INSUFFICIENT_INDEPENDENT_ORIGINS"
    NO_BATCH_EVIDENCE = "NO_BATCH_EVIDENCE"
    DOMINANT_CONTRADICTION = "DOMINANT_CONTRADICTION"
    BELOW_PROMOTION_THRESHOLD = "BELOW_PROMOTION_THRESHOLD"
    ALREADY_KNOWN = "ALREADY_KNOWN"
    OUTSIDE_TARGET_PROGRAM = "OUTSIDE_TARGET_PROGRAM"


class DiscoveryZeroState(str, Enum):
    NOT_ZERO = "not_zero"
    HEALTHY_ZERO = "healthy_zero"
    DEGRADED_ZERO = "degraded_zero"
    PIPELINE_ZERO = "pipeline_zero"
    THRESHOLD_ZERO = "threshold_zero"


class FundingClaimKind(str, Enum):
    ROUND_AMOUNT = "round_amount"
    TOTAL_FUNDING = "total_funding"
    TARGET_RAISE = "target_raise"
    VALUATION = "valuation"
    FUND_SIZE = "fund_size"
    REVENUE = "revenue"
    UNKNOWN = "unknown"


class FundingStatus(str, Enum):
    ANNOUNCED = "announced"
    CLOSED = "closed"
    REPORTED = "reported"
    TARGETING = "targeting"
    RUMORED = "rumored"
    UNKNOWN = "unknown"


class InvestorRelationship(str, Enum):
    PARTICIPATED = "participated"
    INVESTED = "invested"
    LED = "led"
    CO_LED = "co_led"
    BACKED = "backed"
    PORTFOLIO_COMPANY = "portfolio_company"
    MENTIONED = "mentioned"


class BatchDiscoveryObservation(BaseModel):
    """Immutable public observation; a query string is never an identity."""

    observation_id: str = Field(default_factory=_id)
    run_id: str
    program_id: str
    batch_id: str
    candidate_kind: CandidateKind
    candidate_name: str | None = None
    company_name: str | None = None
    claim: str
    stance: ObservationStance
    source_roles: list[SourceTaxonomyRole]
    source_url: str
    canonical_url: str
    source_domain: str
    source_provider: str
    acquisition_providers: list[str] = Field(default_factory=list)
    query: str
    origin_key: str
    origin_url: str = ""
    publisher: str | None = None
    claim_fingerprint: str = ""
    referenced_source: str | None = None
    derivative_of: str | None = None
    independence: SourceIndependence = SourceIndependence.UNKNOWN
    source_reliability: float = Field(ge=0, le=1)
    identity_confidence: float = Field(ge=0, le=1)
    directness: float = Field(ge=0, le=1)
    temporal_relevance: float = Field(ge=0, le=1)
    published_at: datetime | None = None
    observed_at: datetime = Field(default_factory=_now)
    retrieved_at: datetime = Field(default_factory=_now)
    aliases: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    cofounders: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    raw_observation: dict[str, Any] = Field(default_factory=dict)

    @field_validator("published_at", "observed_at", "retrieved_at")
    @classmethod
    def aware_timestamps(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None


class ConfidenceComponent(BaseModel):
    component: str
    effect: float
    explanation: str
    observation_ids: list[str] = Field(default_factory=list)


class SourceCoverageItem(BaseModel):
    family: str
    status: str
    applicable: bool = True
    applicability_reason: str = "Applicable to this investigation."
    supporting: int = Field(default=0, ge=0)
    contradicting: int = Field(default=0, ge=0)
    checked_no_evidence: bool = False
    unavailable_reason: str | None = None


class SourceCoverage(BaseModel):
    applicable_families: int = Field(default=1, ge=1)
    checked_families: int = Field(default=0, ge=0)
    supporting_families: int = Field(default=0, ge=0)
    contradicting_families: int = Field(default=0, ge=0)
    unavailable_families: int = Field(default=0, ge=0)
    permission_required_families: int = Field(default=0, ge=0)
    coverage_pct: float = Field(default=0, ge=0, le=100)
    families: list[SourceCoverageItem] = Field(default_factory=list)


class FundingRoundEvidence(BaseModel):
    amount: str | None = None
    round: str | None = None
    claim_kind: FundingClaimKind = FundingClaimKind.UNKNOWN
    status: FundingStatus = FundingStatus.UNKNOWN
    round_type: str = "UNKNOWN"
    company_name: str | None = None
    claim: str = ""
    announced_at: datetime | None = None
    investors: list[str] = Field(default_factory=list)
    lead_investor: str | None = None
    source_url: str
    conflict_group: str | None = None
    conflicts_with: list[str] = Field(default_factory=list)

    @field_validator("announced_at")
    @classmethod
    def aware_funding_timestamp(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None


class InvestorSignal(BaseModel):
    investor: str
    partner: str | None = None
    relationship: InvestorRelationship = InvestorRelationship.MENTIONED
    source_url: str
    claim: str = ""


class BatchParticipationAssessment(BaseModel):
    confidence_pct: int = Field(ge=0, le=95)
    identity_confidence_pct: int = Field(ge=0, le=100)
    independent_supporting_sources: int = Field(ge=0)
    supporting_observations: int = Field(ge=0)
    contradictory_observations: int = Field(ge=0)
    other_batch_observations: int = Field(ge=0)
    ambiguous_observations: int = Field(ge=0)
    stale_observations: int = Field(ge=0)
    derivative_observations: int = Field(ge=0)
    source_diversity: list[str] = Field(default_factory=list)
    components: list[ConfidenceComponent] = Field(default_factory=list)
    promotion_eligible: bool = False
    promotion_blockers: list[str] = Field(default_factory=list)
    identity_corroborated: bool = False
    batch_evidence_present: bool = False
    accelerator_investigation_state: str = "needs_accelerator_investigation"
    coverage_label: str = "LOW COVERAGE"
    next_best_checks: list[str] = Field(default_factory=list)
    source_coverage: SourceCoverage = Field(default_factory=SourceCoverage)


class CandidateProfile(BaseModel):
    founder_name: str | None = None
    company_name: str | None = None
    website: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    github_username: str | None = None
    technical_profiles: list[str] = Field(default_factory=list)
    publications: list[str] = Field(default_factory=list)
    background_claims: list[str] = Field(default_factory=list)
    cofounders: list[str] = Field(default_factory=list)
    funding_rounds: list[FundingRoundEvidence] = Field(default_factory=list)
    financial_claims: list[FundingRoundEvidence] = Field(default_factory=list)
    investor_signals: list[InvestorSignal] = Field(default_factory=list)
    investment_attention_pct: int = Field(default=0, ge=0, le=95)
    investment_attention_label: str = "none"
    completeness_pct: int = Field(default=0, ge=0, le=100)


class CandidateHypothesis(BaseModel):
    """Versioned cluster of observations that may represent one identity."""

    hypothesis_id: str
    revision_id: str
    revision: int = Field(ge=1)
    run_id: str
    program_id: str
    batch_id: str
    candidate_kind: CandidateKind
    display_name: str
    candidate_name: str | None = None
    company_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    cofounders: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    state: CandidateState
    assessment: BatchParticipationAssessment
    profile: CandidateProfile
    first_seen_at: datetime
    last_updated_at: datetime
    review_state: str = "unreviewed"
    promoted_person_source_id: str | None = None
    promoted_company_source_id: str | None = None
    canonical_person_id: str | None = None
    canonical_company_id: str | None = None
    input_digest: str
    why_now: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("first_seen_at", "last_updated_at")
    @classmethod
    def aware_hypothesis_timestamps(cls, value: datetime) -> datetime:
        return _aware(value)


class SourceYieldMetrics(BaseModel):
    provider_id: str
    source_family: str
    stage: DiscoveryStage
    items_retrieved: int = Field(default=0, ge=0)
    relevant_items: int = Field(default=0, ge=0)
    venture_observations: int = Field(default=0, ge=0)
    candidate_hypotheses_created: int = Field(default=0, ge=0)
    candidate_hypotheses_updated: int = Field(default=0, ge=0)
    independent_evidence_origins: int = Field(default=0, ge=0)
    promotions_contributed_to: int = Field(default=0, ge=0)
    funding_claims: int = Field(default=0, ge=0)
    investor_claims: int = Field(default=0, ge=0)
    duplicates: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    rejection_reasons: dict[DiscoveryDropReason, int] = Field(default_factory=dict)


class WatchlistYieldMetrics(BaseModel):
    provider_id: str
    watchlist_id: str
    publication_or_actor: str
    items_checked: int = Field(default=0, ge=0)
    new_items: int = Field(default=0, ge=0)
    relevant_items: int = Field(default=0, ge=0)
    venture_observations: int = Field(default=0, ge=0)
    candidate_hypotheses_created: int = Field(default=0, ge=0)
    candidate_hypotheses_updated: int = Field(default=0, ge=0)
    funding_claims: int = Field(default=0, ge=0)
    investor_claims: int = Field(default=0, ge=0)
    duplicates: int = Field(default=0, ge=0)


class DiscoveryFunnel(BaseModel):
    retrieved_items: int = Field(default=0, ge=0)
    relevant_items: int = Field(default=0, ge=0)
    venture_observations: int = Field(default=0, ge=0)
    candidate_hypotheses_created: int = Field(default=0, ge=0)
    candidate_hypotheses_updated: int = Field(default=0, ge=0)
    identity_corroborated: int = Field(default=0, ge=0)
    batch_evidence_present: int = Field(default=0, ge=0)
    promotion_eligible: int = Field(default=0, ge=0)
    canonical_promotions: int = Field(default=0, ge=0)
    drop_reasons: dict[DiscoveryDropReason, int] = Field(default_factory=dict)
    source_yield: list[SourceYieldMetrics] = Field(default_factory=list)
    watchlist_yield: list[WatchlistYieldMetrics] = Field(default_factory=list)

class BatchSourceOutcome(BaseModel):
    provider_id: str
    source_family: str = "general_public_web"
    stage: DiscoveryStage
    status: SourceOperationStatus
    queries_attempted: int = Field(default=0, ge=0)
    results_seen: int = Field(default=0, ge=0)
    observations_accepted: int = Field(default=0, ge=0)
    relevant_items: int = Field(default=0, ge=0)
    rejection_reasons: dict[DiscoveryDropReason, int] = Field(default_factory=dict)
    platform_metrics: dict[str, dict[str, int]] = Field(default_factory=dict)
    request_attempts: int = Field(default=0, ge=0)
    retry_requests: int = Field(default=0, ge=0)
    redirect_requests: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    error: str | None = None


class BatchDiscoveryRequest(BaseModel):
    program_id: str | None = None
    batch_id: str | None = None
    budget: ResearchBudget | None = None
    include_directory_verification: bool = True


class BatchDiscoveryRun(BaseModel):
    schema_version: str = "9.1"
    engine_version: str = "intelligence-quality-1"
    run_id: str = Field(default_factory=_id)
    program_id: str
    batch_id: str
    stage: DiscoveryStage = DiscoveryStage.DISCOVERY
    status: str = "running"
    queries: list[str] = Field(default_factory=list)
    source_outcomes: list[BatchSourceOutcome] = Field(default_factory=list)
    observations: list[BatchDiscoveryObservation] = Field(default_factory=list)
    hypotheses: list[CandidateHypothesis] = Field(default_factory=list)
    funnel: DiscoveryFunnel = Field(default_factory=DiscoveryFunnel)
    zero_state: DiscoveryZeroState = DiscoveryZeroState.NOT_ZERO
    promoted_count: int = 0
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    stop_reason: str = "completed"
    budget: ResearchBudget
    request_accounting: dict[str, Any] = Field(default_factory=dict)
    input_digest: str = ""

    @field_validator("started_at", "finished_at")
    @classmethod
    def aware_run_timestamps(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None


class BatchDiscoveryListResponse(BaseModel):
    program_id: str
    batch_id: str
    candidates: list[CandidateHypothesis]
    source_contract: str = (
        "Discovery proposes candidates; verification establishes identity and batch; "
        "enrichment cannot promote a candidate."
    )


class BatchCandidateDetailResponse(BaseModel):
    candidate: CandidateHypothesis
    observations: list[BatchDiscoveryObservation]
    immutable_evidence: bool = True
    historical_cutoff_field: str = "observed_at"


class ManualBatchObservationRequest(BaseModel):
    program_id: str
    candidate_kind: CandidateKind
    candidate_name: str | None = None
    company_name: str | None = None
    claim: str = Field(min_length=1, max_length=2_000)
    source_url: str
    stance: ObservationStance
    source_roles: list[SourceTaxonomyRole]
    published_at: datetime | None = None
    aliases: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    cofounders: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_candidate_identity(self) -> ManualBatchObservationRequest:
        if self.candidate_kind is CandidateKind.PERSON:
            if not self.candidate_name or not self.company_name:
                raise ValueError("person observations require candidate_name and company_name")
        elif not self.company_name:
            raise ValueError("company observations require company_name")
        if not self.source_roles:
            raise ValueError("source_roles must not be empty")
        return self

    @field_validator("published_at")
    @classmethod
    def aware_manual_timestamp(cls, value: datetime | None) -> datetime | None:
        return _aware(value) if value is not None else None


class BatchDiscoveryReplayResult(BaseModel):
    program_id: str
    batch_id: str
    cutoff: datetime
    historical_semantics: str = "observed_at <= cutoff"
    observations_selected: int
    hypotheses: list[CandidateHypothesis]
    input_digest: str
    live_state_mutated: bool = False

    @field_validator("cutoff")
    @classmethod
    def aware_cutoff(cls, value: datetime) -> datetime:
        return _aware(value)


class OsintToolDecision(str, Enum):
    INTEGRATE = "INTEGRATE"
    ADAPTER_ONLY = "ADAPTER_ONLY"
    DEFER = "DEFER"
    REJECT = "REJECT"


class OsintToolBenchmark(BaseModel):
    tool: str
    version_checked: str
    license: str
    decision: OsintToolDecision
    taxonomy_roles: list[SourceTaxonomyRole]
    incremental_discoveries: int
    useful_corroborations: int
    false_matches: int
    request_cost: int
    runtime_seconds: float
    structured_output_quality: int = Field(ge=0, le=100)
    provenance_quality: int = Field(ge=0, le=100)
    policy_compatibility: int = Field(ge=0, le=100)
    reasoning: str


class BatchDiscoverySourceCapability(BaseModel):
    source_id: str
    display_name: str
    taxonomy_roles: list[SourceTaxonomyRole]
    access_modes: list[SourceAccessMode]
    seeds_unknown_discovery: bool
    direct_restricted_social_access: bool = False
    notes: str


class BatchDiscoveryContractResponse(BaseModel):
    schema_version: str = "8.0"
    stages: list[DiscoveryStage]
    source_taxonomy: list[SourceTaxonomyRole]
    promotion_min_confidence_pct: int = 72
    promotion_min_independent_sources: int = 2
    directory_role: str = "verification_and_fallback_seed_only"
    restricted_social_mode: str = "permitted_search_index_discovery_only"
    external_tools_execute_network_requests: bool = False
    benchmark_mode: str = "deterministic_offline_representative_fixtures"
    source_capabilities: list[BatchDiscoverySourceCapability]
    tool_benchmarks: list[OsintToolBenchmark]


class BatchDiscoveryEvaluation(BaseModel):
    precision_at_k: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    false_positive_rate: float = Field(ge=0, le=1)
    mean_lead_time_days: float | None = None
    missed_entities: list[str] = Field(default_factory=list)
    false_positive_entities: list[str] = Field(default_factory=list)
    identity_resolution_errors: list[str] = Field(default_factory=list)
    source_contribution: dict[str, int] = Field(default_factory=dict)
    source_ablation_promotion_loss: dict[str, int] = Field(default_factory=dict)
    acceptance_confidence_brier_score: float | None = Field(default=None, ge=0, le=1)
