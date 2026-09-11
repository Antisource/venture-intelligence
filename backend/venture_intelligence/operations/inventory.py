"""Complete non-secret inventory of implemented source acquisition boundaries."""

from __future__ import annotations

from venture_intelligence.config import settings
from venture_intelligence.operations.models import (
    ConnectorAccessMode,
    ConnectorConfigurationState,
    ConnectorImplementationState,
    ConnectorInventoryItem,
    DedicatedOsintToolDiagnostic,
    LiveSourceDiagnostic,
    OfficialApiImplementationStatus,
    RecommendedUiVisibility,
)
from venture_intelligence.research.provider_policy import (
    BUILTIN_PROVIDER_POLICIES,
    CONNECTOR_POLICIES,
    OPTIONAL_INTEGRATION_POLICIES,
    EvidenceRole,
    SourceAccessMethod,
)

_EXTERNAL_OFFICIAL_API: dict[str, bool | None] = {
    "arxiv": True,
    "wayback": True,
    "github": True,
    "discord": True,
    "discord_authorized_api": True,
    "firecrawl": True,
    "product_hunt": True,
    "linkedin": True,
    "social": True,
    "brave": True,
}
_BOUNDARY_ONLY = {"discord_authorized_api", "firecrawl"}
_OFFICIAL_API_NOT_IMPLEMENTED = {"linkedin", "social", "brave"}


def _access_mode(provider_id: str, method: SourceAccessMethod) -> ConnectorAccessMode:
    if provider_id == "discord":
        return ConnectorAccessMode.PUBLIC_EMBED
    if provider_id == "discord_authorized_api":
        return ConnectorAccessMode.AUTHORIZED_OFFICIAL_API
    if provider_id in {"maigret", "sherlock"}:
        return ConnectorAccessMode.LOCAL_OPTIONAL_TOOL
    if provider_id == "product_hunt":
        return ConnectorAccessMode.AUTHORIZED_OFFICIAL_API
    if provider_id == "social":
        return ConnectorAccessMode.RESTRICTED_DIRECT_ACCESS_DISABLED
    if provider_id == "linkedin":
        return ConnectorAccessMode.PERMISSION_REQUIRED
    if method is SourceAccessMethod.SEARCH_DISCOVERY:
        return ConnectorAccessMode.INDEXED_PUBLIC_DISCOVERY
    if method is SourceAccessMethod.RSS:
        return ConnectorAccessMode.PUBLIC_FEED
    if method is SourceAccessMethod.MANUAL_IMPORT:
        return ConnectorAccessMode.MANUAL_IMPORT
    if provider_id == "github":
        return ConnectorAccessMode.PUBLIC_WITH_OPTIONAL_AUTH
    return ConnectorAccessMode.PUBLIC_NO_CREDENTIAL


def _configured(provider_id: str, tool: DedicatedOsintToolDiagnostic | None) -> bool:
    if provider_id == "product_hunt":
        token = settings.product_hunt_access_token
        return bool(
            settings.product_hunt_api_enabled
            and token is not None
            and token.get_secret_value().strip()
        )
    if provider_id == "firecrawl":
        return bool(settings.firecrawl_api_url and settings.firecrawl_api_key)
    if provider_id == "discord_authorized_api":
        token = settings.discord_bot_token
        authorized_scope = bool(
            settings.discord_authorized_guild_ids.strip()
            or settings.discord_authorized_channel_ids.strip()
        )
        return bool(
            settings.discord_api_enabled
            and token is not None
            and token.get_secret_value().strip()
            and authorized_scope
        )
    if tool is not None:
        return tool.installed
    return provider_id != "discord"


def _yield_status(*, diagnostic: LiveSourceDiagnostic | None, yield_count: int) -> str:
    if yield_count:
        return "OBSERVED_POSITIVE"
    if diagnostic is not None:
        return "OBSERVED_ZERO"
    return "NOT_RUN"


def _diagnostic_for(
    source_id: str,
    diagnostics: list[LiveSourceDiagnostic],
) -> LiveSourceDiagnostic | None:
    exact = [item for item in diagnostics if item.source_id.split(":", 1)[0] == source_id]
    controls = [item for item in exact if item.known_positive_control]
    return controls[-1] if controls else (exact[-1] if exact else None)


def build_connector_inventory(
    diagnostics: list[LiveSourceDiagnostic],
    osint_tools: list[DedicatedOsintToolDiagnostic],
) -> list[ConnectorInventoryItem]:
    """Reconcile every declared provider/connector with measured health and yield."""
    tool_diagnostics = {item.tool.casefold(): item for item in osint_tools}
    inventory: list[ConnectorInventoryItem] = []
    seen: set[str] = set()
    for policy in (
        *BUILTIN_PROVIDER_POLICIES,
        *CONNECTOR_POLICIES,
        *OPTIONAL_INTEGRATION_POLICIES,
    ):
        if policy.provider_id in seen:
            continue
        seen.add(policy.provider_id)
        diagnostic = _diagnostic_for(policy.provider_id, diagnostics)
        tool = tool_diagnostics.get(policy.provider_id)
        uses_official_api = policy.access_method is SourceAccessMethod.PUBLIC_API
        configured = _configured(policy.provider_id, tool)
        if policy.provider_id in _BOUNDARY_ONLY:
            api_status = OfficialApiImplementationStatus.OFFICIAL_API_BOUNDARY_ONLY
        elif policy.provider_id in _OFFICIAL_API_NOT_IMPLEMENTED:
            api_status = OfficialApiImplementationStatus.OFFICIAL_API_NOT_SUPPORTED
        elif uses_official_api and diagnostic and diagnostic.state.value == "healthy":
            api_status = OfficialApiImplementationStatus.OFFICIAL_API_LIVE_VALIDATED
        elif uses_official_api:
            api_status = (
                OfficialApiImplementationStatus.OFFICIAL_API_IMPLEMENTED_NOT_CONFIGURED
            )
        else:
            api_status = OfficialApiImplementationStatus.OFFICIAL_API_NOT_SUPPORTED
        if tool is not None:
            live_validation = tool.execution_status
            health = (
                "healthy"
                if tool.execution_status in {"LIVE_VALIDATED", "HEALTHY_EMPTY"}
                else "unavailable"
            )
            yield_count = tool.accepted
            known_positive = tool.execution_status
        else:
            live_validation = (
                diagnostic.operation_status.value if diagnostic else "NOT_RUN"
            )
            health = diagnostic.state.value if diagnostic else "not_run"
            yield_count = diagnostic.results if diagnostic else 0
            known_positive = (
                diagnostic.operation_status.value
                if diagnostic and diagnostic.known_positive_control
                else "NOT_RUN"
            )
        inventory.append(
            ConnectorInventoryItem(
                connector=policy.provider_id,
                source_family=(
                    policy.capabilities[0].value if policy.capabilities else "unknown"
                ),
                roles=[item.value for item in policy.capabilities],
                default_enabled=policy.default_enabled,
                access_mode=_access_mode(policy.provider_id, policy.access_method),
                implementation_state=(
                    ConnectorImplementationState.BOUNDARY_ONLY
                    if policy.provider_id in _BOUNDARY_ONLY
                    else ConnectorImplementationState.ORCHESTRATION_ONLY
                    if policy.provider_id == "triangulation"
                    else ConnectorImplementationState.IMPLEMENTED_ACTIVE
                    if policy.default_enabled
                    else ConnectorImplementationState.IMPLEMENTED_DISABLED_BY_DEFAULT
                ),
                official_api_available_externally=_EXTERNAL_OFFICIAL_API.get(
                    policy.provider_id
                ),
                official_api_implementation_status=api_status,
                credentials_required=policy.requires_credentials,
                configuration_state=(
                    ConnectorConfigurationState.CONFIGURED
                    if configured and policy.requires_credentials
                    else ConnectorConfigurationState.USER_CONFIGURATION_REQUIRED
                    if not configured
                    else ConnectorConfigurationState.READY_NO_CREDENTIALS
                ),
                credential_configuration=policy.configuration_requirements,
                known_positive_control=known_positive,
                live_validation=live_validation,
                health=health,
                yield_count=yield_count,
                source_yield=_yield_status(
                    diagnostic=diagnostic,
                    yield_count=yield_count,
                ),
                source_coverage_behavior=(
                    "ORCHESTRATION_ONLY_EXCLUDED_FROM_COVERAGE"
                    if policy.provider_id == "triangulation"
                    else "COUNT_DIRECT_ACQUISITION_ONCE"
                ),
                recommended_ui_visibility=(
                    RecommendedUiVisibility.DIAGNOSTIC
                    if policy.provider_id
                    in {
                        "triangulation",
                        "social",
                        "linkedin",
                        "discord_authorized_api",
                        "firecrawl",
                        "maigret",
                        "sherlock",
                    }
                    else RecommendedUiVisibility.PRIMARY
                ),
                fallback=policy.fallback,
                evidence_authority=(
                    "discovery_only"
                    if policy.evidence_role is EvidenceRole.DISCOVERY
                    else policy.evidence_role.value
                ),
                limitations=policy.rate_limit_note,
            )
        )
    for platform in ("linkedin", "x", "instagram", "threads"):
        source_id = f"indexed_{platform}"
        diagnostic = _diagnostic_for(source_id, diagnostics)
        inventory.append(
            ConnectorInventoryItem(
                connector=source_id,
                source_family="indexed_social",
                roles=["discovery", "social_announcement"],
                default_enabled=True,
                access_mode=ConnectorAccessMode.INDEXED_PUBLIC_DISCOVERY,
                implementation_state=ConnectorImplementationState.IMPLEMENTED_ACTIVE,
                official_api_available_externally=True,
                official_api_implementation_status=(
                    OfficialApiImplementationStatus.OFFICIAL_API_NOT_SUPPORTED
                ),
                credentials_required=False,
                configuration_state=ConnectorConfigurationState.READY_NO_CREDENTIALS,
                known_positive_control=(
                    diagnostic.operation_status.value if diagnostic else "NOT_RUN"
                ),
                live_validation=(
                    diagnostic.operation_status.value if diagnostic else "NOT_RUN"
                ),
                health=diagnostic.state.value if diagnostic else "not_run",
                yield_count=diagnostic.results if diagnostic else 0,
                source_yield=_yield_status(
                    diagnostic=diagnostic,
                    yield_count=diagnostic.results if diagnostic else 0,
                ),
                source_coverage_behavior=(
                    "ATTRIBUTED_TO_SEARCH_PROVIDER_NOT_DOUBLE_COUNTED"
                ),
                recommended_ui_visibility=RecommendedUiVisibility.PRIMARY,
                fallback="Retain indexed public references; direct platform crawling stays off.",
                evidence_authority="discovery_only",
                limitations=(
                    "Official platform API and direct website automation are not implemented "
                    "by this acquisition path."
                ),
            )
        )
    return inventory
