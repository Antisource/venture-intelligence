"""Typed M9 contracts for gated live validation and source quality."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from venture_intelligence.batch_discovery.models import DiscoveryFunnel, DiscoveryZeroState
from venture_intelligence.shared.models import ResearchBudget, SourceOperationStatus


class SourceQualityState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    EMPTY = "empty"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
    DRIFTED = "drifted"
    NOT_RUN = "not_run"


class OfficialApiImplementationStatus(str, Enum):
    OFFICIAL_API_LIVE_VALIDATED = "OFFICIAL_API_LIVE_VALIDATED"
    OFFICIAL_API_IMPLEMENTED_NOT_CONFIGURED = "OFFICIAL_API_IMPLEMENTED_NOT_CONFIGURED"
    OFFICIAL_API_BOUNDARY_ONLY = "OFFICIAL_API_BOUNDARY_ONLY"
    OFFICIAL_API_NOT_SUPPORTED = "OFFICIAL_API_NOT_SUPPORTED"

    @classmethod
    def _missing_(cls, value: object):
        compatibility = {
            "IMPLEMENTED_NOT_CONFIGURED": cls.OFFICIAL_API_IMPLEMENTED_NOT_CONFIGURED,
            "BOUNDARY_ONLY": cls.OFFICIAL_API_BOUNDARY_ONLY,
            "NOT_SUPPORTED": cls.OFFICIAL_API_NOT_SUPPORTED,
        }
        return compatibility.get(str(value))


class ConnectorAccessMode(str, Enum):
    PUBLIC_NO_CREDENTIAL = "PUBLIC_NO_CREDENTIAL"
    PUBLIC_WITH_OPTIONAL_AUTH = "PUBLIC_WITH_OPTIONAL_AUTH"
    AUTHORIZED_OFFICIAL_API = "AUTHORIZED_OFFICIAL_API"
    INDEXED_PUBLIC_DISCOVERY = "INDEXED_PUBLIC_DISCOVERY"
    PUBLIC_FEED = "PUBLIC_FEED"
    PUBLIC_EMBED = "PUBLIC_EMBED"
    MANUAL_IMPORT = "MANUAL_IMPORT"
    LOCAL_OPTIONAL_TOOL = "LOCAL_OPTIONAL_TOOL"
    PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    RESTRICTED_DIRECT_ACCESS_DISABLED = "RESTRICTED_DIRECT_ACCESS_DISABLED"

    @classmethod
    def _missing_(cls, value: object):
        compatibility = {
            "public_api": cls.PUBLIC_NO_CREDENTIAL,
            "public_dataset": cls.PUBLIC_NO_CREDENTIAL,
            "public_html": cls.PUBLIC_NO_CREDENTIAL,
            "rendered_public": cls.PUBLIC_NO_CREDENTIAL,
            "search_discovery": cls.INDEXED_PUBLIC_DISCOVERY,
            "rss": cls.PUBLIC_FEED,
            "manual_import": cls.MANUAL_IMPORT,
            "local_tool": cls.LOCAL_OPTIONAL_TOOL,
        }
        return compatibility.get(str(value))


class ConnectorImplementationState(str, Enum):
    IMPLEMENTED_ACTIVE = "IMPLEMENTED_ACTIVE"
    IMPLEMENTED_DISABLED_BY_DEFAULT = "IMPLEMENTED_DISABLED_BY_DEFAULT"
    BOUNDARY_ONLY = "BOUNDARY_ONLY"
    ORCHESTRATION_ONLY = "ORCHESTRATION_ONLY"


class ConnectorConfigurationState(str, Enum):
    READY_NO_CREDENTIALS = "READY_NO_CREDENTIALS"
    CONFIGURED = "CONFIGURED"
    USER_CONFIGURATION_REQUIRED = "USER_CONFIGURATION_REQUIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RecommendedUiVisibility(str, Enum):
    PRIMARY = "PRIMARY"
    DIAGNOSTIC = "DIAGNOSTIC"
    HIDDEN = "HIDDEN"


class ConnectorInventoryItem(BaseModel):
    connector: str
    source_family: str
    roles: list[str]
    default_enabled: bool
    access_mode: ConnectorAccessMode
    implementation_state: ConnectorImplementationState = (
        ConnectorImplementationState.IMPLEMENTED_ACTIVE
    )
    official_api_available_externally: bool | None = None
    official_api_implementation_status: OfficialApiImplementationStatus
    credentials_required: bool = False
    configuration_state: ConnectorConfigurationState = (
        ConnectorConfigurationState.READY_NO_CREDENTIALS
    )
    credential_configuration: list[str] = Field(default_factory=list)
    known_positive_control: str = "NOT_RUN"
    live_validation: str = "NOT_RUN"
    health: str = "not_run"
    yield_count: int = Field(default=0, ge=0)
    source_yield: str = "NOT_RUN"
    source_coverage_behavior: str = "COUNT_DIRECT_ACQUISITION_ONCE"
    recommended_ui_visibility: RecommendedUiVisibility = RecommendedUiVisibility.PRIMARY
    fallback: str | None = None
    evidence_authority: str
    limitations: str | None = None


class LiveSourceDiagnostic(BaseModel):
    source_id: str
    state: SourceQualityState
    operation_status: SourceOperationStatus
    discovery_role: str
    validation_scope: str = "normal_live_discovery"
    known_positive_control: bool = False
    enabled: bool = True
    configured: bool = True
    credential_configured: bool = False
    access_mode: str = "public"
    requests: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    redirects: int = Field(default=0, ge=0)
    results: int = Field(default=0, ge=0)
    relevant_items: int = Field(default=0, ge=0)
    accepted_observations: int = Field(default=0, ge=0)
    rejected_items: int = Field(default=0, ge=0)
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    source_family: str = "general_public_web"
    unique_origins: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    indexed_social_hosts: list[str] = Field(default_factory=list)
    direct_restricted_social_requests: int = Field(default=0, ge=0)
    final_urls: list[str] = Field(default_factory=list)
    http_status: int | None = None
    auth_state: str = "not_required"
    rate_limit_limit: int | None = Field(default=None, ge=0)
    rate_limit_remaining: int | None = Field(default=None, ge=0)
    never_attempted_reason: str | None = None
    error: str | None = None
    drift_reason: str | None = None
    retryable: bool = False


class SocialPlatformDiagnostic(BaseModel):
    platform: str
    enabled: bool = True
    access_mode: str = "indexed_public_reference"
    acquisition_providers: list[str] = Field(default_factory=list)
    retrieved: int = Field(default=0, ge=0)
    relevant: int = Field(default=0, ge=0)
    observations: int = Field(default=0, ge=0)
    hypotheses: int = Field(default=0, ge=0)
    duplicates: int = Field(default=0, ge=0)
    corroborated: int = Field(default=0, ge=0)
    unavailable: bool = False
    unavailability_reasons: list[str] = Field(default_factory=list)
    direct_requests: int = Field(default=0, ge=0)


class DedicatedOsintToolDiagnostic(BaseModel):
    tool: str
    installed: bool
    enabled: bool = False
    execution_status: str
    input: str | None = None
    sites_attempted: int = Field(default=0, ge=0)
    public_profiles_returned: int = Field(default=0, ge=0)
    accepted: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    ambiguous: int = Field(default=0, ge=0)
    duplicates: int = Field(default=0, ge=0)
    execution_time_ms: float = Field(default=0, ge=0)
    failure_reason: str | None = None
    observations_created: int = Field(default=0, ge=0)
    public_profile_urls: list[str] = Field(default_factory=list)
    identity_effect: str = "none"
    install_requirement: str | None = None


class LiveCandidateDiagnostic(BaseModel):
    hypothesis_id: str
    candidate_name: str | None = None
    company_name: str | None = None
    state: str
    confidence_pct: int = Field(ge=0, le=95)
    identity_confidence_pct: int = Field(ge=0, le=95)
    batch_evidence_present: bool
    promotion_eligible: bool
    promotion_blockers: list[str] = Field(default_factory=list)
    observation_count: int = Field(default=0, ge=0)
    source_urls: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)


class LiveDiscoveryQuality(BaseModel):
    unknown_hypotheses: int = Field(default=0, ge=0)
    promotion_eligible: int = Field(default=0, ge=0)
    canonical_promotions: int = Field(default=0, ge=0)
    precision_at_k: float | None = Field(default=None, ge=0, le=1)
    recall: float | None = Field(default=None, ge=0, le=1)
    false_positive_rate: float | None = Field(default=None, ge=0, le=1)
    mean_lead_time_days: float | None = None
    source_contribution: dict[str, int] = Field(default_factory=dict)
    source_ablation_promotion_loss: dict[str, int] = Field(default_factory=dict)
    ground_truth_available: bool = False
    ground_truth_note: str
    funnel: DiscoveryFunnel = Field(default_factory=DiscoveryFunnel)
    zero_state: DiscoveryZeroState = DiscoveryZeroState.NOT_ZERO
    candidate_examples: list[LiveCandidateDiagnostic] = Field(default_factory=list)


class OperationalValidationReport(BaseModel):
    schema_version: str = "9.4"
    run_id: str
    program_id: str
    batch_id: str
    mode: str = "gated_live_read_only"
    live_network_executed: bool
    operational_store_mutated: bool = False
    temporary_store_isolated: bool = True
    direct_social_access_enabled: bool = False
    started_at: datetime
    finished_at: datetime
    budget: ResearchBudget
    control_budget_usage: dict = Field(default_factory=dict)
    discovery_budget_usage: dict = Field(default_factory=dict)
    stop_reason: str
    source_diagnostics: list[LiveSourceDiagnostic]
    connector_inventory: list[ConnectorInventoryItem] = Field(default_factory=list)
    social_platform_diagnostics: list[SocialPlatformDiagnostic] = Field(default_factory=list)
    dedicated_osint_tools: list[DedicatedOsintToolDiagnostic] = Field(default_factory=list)
    osint_public_profile_origins: int = Field(default=0, ge=0)
    osint_cross_tool_duplicates: int = Field(default=0, ge=0)
    discovery_quality: LiveDiscoveryQuality
    blocked_or_unavailable_sources: list[str] = Field(default_factory=list)
    fixture_only_capabilities: list[str] = Field(default_factory=list)
    operational_blind_spots: list[str] = Field(default_factory=list)
    report_digest: str


class SourceQualityResponse(BaseModel):
    available: bool
    report: OperationalValidationReport | None = None
    message: str


class OperationalValidationContract(BaseModel):
    schema_version: str = "9.4"
    execution_boundary: str = "local_cli_only"
    cli_command: str
    required_gate: str = "VENTURE_INTELLIGENCE_LIVE_TESTS=1 plus --allow-network"
    read_only: bool = True
    maximum_requests: int = 24
    direct_social_access_enabled: bool = False
    metrics_without_ground_truth: list[str]
    metrics_requiring_ground_truth: list[str]
