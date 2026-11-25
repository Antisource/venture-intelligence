"""Source policies, runtime settings, source quality, and readiness."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from venture_intelligence.config import settings
from venture_intelligence.operations.inventory import build_connector_inventory
from venture_intelligence.operations.models import (
    ConnectorInventoryItem,
    OperationalValidationContract,
    SourceQualityResponse,
)
from venture_intelligence.operations.readiness import SystemReadiness, system_readiness
from venture_intelligence.operations.store import SourceQualityReportStore
from venture_intelligence.research.provider_policy import (
    BUILTIN_PROVIDER_POLICIES,
    CONNECTOR_POLICIES,
    OPTIONAL_INTEGRATION_POLICIES,
    ProviderPolicy,
)
from venture_intelligence.research.watchlist import (
    IntelligenceWatchlist,
    SourceSettingsStore,
    SourceStateStore,
    WatchlistEntry,
)

router = APIRouter(prefix="/api/v1", tags=["v1"])


class SourcePolicyResponse(BaseModel):
    providers: list[ProviderPolicy]
    connectors: list[ProviderPolicy]
    inventory: list[ConnectorInventoryItem]
    watchlists: list[WatchlistEntry] = Field(default_factory=list)
    runtime: list["SourceRuntimeStatus"] = Field(default_factory=list)
    direct_social_access_enabled: bool = False
    discovery_boundary: str


class SourceRuntimeStatus(BaseModel):
    source_id: str
    enabled: bool
    health: str = "not_checked"
    last_checked_at: str | None = None
    last_successful_fetch_at: str | None = None
    new_item_count: int = 0
    manually_overridden: bool = False
    note: str | None = None
    configuration_status: str = "not_required"
    credential_present: bool | None = None


class SourceSettingRequest(BaseModel):
    enabled: bool


@router.get("/sources", response_model=SourcePolicyResponse)
async def list_source_policies() -> SourcePolicyResponse:
    """Return non-secret, machine-readable source access policy metadata."""
    policies = [policy.model_copy(deep=True) for policy in BUILTIN_PROVIDER_POLICIES]
    overrides_store = SourceSettingsStore(settings.source_settings_path)
    overrides = overrides_store.load()
    source_states = SourceStateStore(settings.source_state_path).load()
    try:
        report = SourceQualityReportStore(settings.source_quality_report_path).load()
    except (OSError, ValueError):
        report = None
    quality = {}
    for item in report.source_diagnostics if report is not None else []:
        source_key = item.source_id.split(":", 1)[0]
        existing = quality.get(source_key)
        if existing is None or item.known_positive_control:
            quality[source_key] = item
    watchlist = IntelligenceWatchlist.load(settings.intelligence_watchlist_path)
    watchlist_urls = {
        "techcrunch": [
            url
            for item in watchlist.active("publication")
            if (item.organization or "").casefold() == "techcrunch"
            for url in item.feed_urls
        ],
        "newsletter_watchlist": [
            url for item in watchlist.active("newsletter") for url in item.feed_urls
        ],
        "vc_watchlist": [
            url for item in watchlist.active("vc_firm", "vc_partner") for url in item.public_urls
        ],
    }
    runtime: list[SourceRuntimeStatus] = []
    for policy in policies:
        states = [
            source_states[url]
            for url in watchlist_urls.get(policy.provider_id, [])
            if url in source_states
        ]
        diagnostic = quality.get(policy.provider_id)
        last_checked = max(
            (item.last_checked_at for item in states if item.last_checked_at),
            default=None,
        )
        last_success = max(
            (item.last_successful_fetch_at for item in states if item.last_successful_fetch_at),
            default=None,
        )
        health = (
            diagnostic.state.value
            if diagnostic is not None
            else "healthy"
            if states and all(item.parse_health == "healthy" for item in states)
            else "degraded"
            if states
            else "not_checked"
        )
        runtime.append(
            SourceRuntimeStatus(
                source_id=policy.provider_id,
                enabled=overrides_store.enabled(policy.provider_id, policy.default_enabled),
                health=health,
                last_checked_at=last_checked,
                last_successful_fetch_at=last_success,
                new_item_count=sum(item.new_item_count for item in states),
                manually_overridden=policy.provider_id in overrides,
                note=(diagnostic.error if diagnostic is not None else None),
            )
        )
    product_hunt_token = settings.product_hunt_access_token
    product_hunt_credential = bool(
        product_hunt_token and product_hunt_token.get_secret_value().strip()
    )
    product_hunt_diagnostic = quality.get("product_hunt")
    product_hunt_checked_at = (
        report.finished_at.isoformat()
        if report is not None and product_hunt_diagnostic is not None
        else None
    )
    product_hunt_health = (
        product_hunt_diagnostic.state.value
        if product_hunt_diagnostic is not None
        else "not_checked"
    )
    runtime.append(
        SourceRuntimeStatus(
            source_id="product_hunt",
            enabled=settings.product_hunt_api_enabled,
            configuration_status=(
                "configured"
                if settings.product_hunt_api_enabled and product_hunt_credential
                else "not_configured"
                if settings.product_hunt_api_enabled
                else "disabled"
            ),
            credential_present=product_hunt_credential,
            health=product_hunt_health,
            last_checked_at=product_hunt_checked_at,
            last_successful_fetch_at=(
                product_hunt_checked_at if product_hunt_health == "healthy" else None
            ),
            note=(product_hunt_diagnostic.error if product_hunt_diagnostic is not None else None),
        )
    )
    return SourcePolicyResponse(
        providers=policies,
        connectors=[
            policy.model_copy(deep=True)
            for policy in (*CONNECTOR_POLICIES, *OPTIONAL_INTEGRATION_POLICIES)
        ],
        inventory=build_connector_inventory(
            report.source_diagnostics if report is not None else [],
            report.dedicated_osint_tools if report is not None else [],
        ),
        watchlists=watchlist.active(),
        runtime=runtime,
        discovery_boundary=(
            "Permitted search provider -> indexed social result is discovery-only; "
            "direct LinkedIn/X/Instagram/Threads website access is disabled by default."
        ),
    )


@router.put("/sources/{source_id}", response_model=SourceRuntimeStatus)
async def update_source_setting(
    source_id: str,
    request: SourceSettingRequest,
) -> SourceRuntimeStatus:
    """Persist an operator override without conflating enabled state with health."""
    policy = next(
        (item for item in BUILTIN_PROVIDER_POLICIES if item.provider_id == source_id),
        None,
    )
    if policy is None:
        raise HTTPException(404, "Unknown configurable source")
    SourceSettingsStore(settings.source_settings_path).set_enabled(source_id, request.enabled)
    return SourceRuntimeStatus(
        source_id=source_id,
        enabled=request.enabled,
        health="not_checked",
        manually_overridden=True,
        note="Manual source setting saved; health is measured independently on the next run.",
    )


@router.get(
    "/source-quality/contract",
    response_model=OperationalValidationContract,
)
async def source_quality_contract() -> OperationalValidationContract:
    """Describe the explicit local-only gate for bounded live validation."""
    return OperationalValidationContract(
        cli_command=(
            "VENTURE_INTELLIGENCE_LIVE_TESTS=1 uv run python -m "
            "venture-intelligence --json sources check --program <program-id> --allow-network"
        ),
        metrics_without_ground_truth=[
            "discovery yield",
            "source contribution",
            "source ablation promotion loss",
            "blocks, drift, latency, retries, and request counts",
        ],
        metrics_requiring_ground_truth=[
            "precision at K",
            "recall",
            "false-positive rate",
            "lead time",
        ],
    )


@router.get("/source-quality", response_model=SourceQualityResponse)
async def source_quality() -> SourceQualityResponse:
    """Return the latest persisted live report; never trigger network access."""
    try:
        report = SourceQualityReportStore(settings.source_quality_report_path).load()
    except (OSError, ValueError) as error:
        return SourceQualityResponse(
            available=False,
            message=f"Stored source-quality report is invalid: {str(error)[:300]}",
        )
    return SourceQualityResponse(
        available=report is not None,
        report=report,
        message=(
            "Latest gated live validation report."
            if report is not None
            else "No gated live validation report has been produced on this installation."
        ),
    )


@router.get("/system/readiness", response_model=SystemReadiness)
async def product_readiness() -> SystemReadiness:
    """Return one non-secret product readiness contract for UI and CLI."""
    return system_readiness()
