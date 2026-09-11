"""Product-level readiness synthesized from truthful local configuration/state."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from venture_intelligence.batch_discovery.tools import local_osint_tool_diagnostics
from venture_intelligence.config import Settings, settings
from venture_intelligence.operations.cycle import OperationsStateStore
from venture_intelligence.operations.store import SourceQualityReportStore
from venture_intelligence.pipeline.email import EmailNotificationService
from venture_intelligence.programs import ProgramRegistry, get_program_registry
from venture_intelligence.research.provider_policy import (
    BUILTIN_PROVIDER_POLICIES,
    CONNECTOR_POLICIES,
    OPTIONAL_INTEGRATION_POLICIES,
    ProviderPolicy,
)
from venture_intelligence.storage.db import Database


class ReadinessStatus(str, Enum):
    READY = "READY"
    OPTIONAL_NOT_CONFIGURED = "OPTIONAL_NOT_CONFIGURED"
    USER_ACTION_REQUIRED = "USER_ACTION_REQUIRED"
    DEGRADED = "DEGRADED"
    BROKEN = "BROKEN"


class ReadinessComponent(BaseModel):
    component: str
    status: ReadinessStatus
    summary: str
    action: str | None = None
    technical_status: str | None = None


class SocialAccessDiagnostic(BaseModel):
    platform: str
    direct_api_available: bool = False
    indexed_discovery_available: bool = True
    manual_import_available: bool = True
    permission_required: bool = True
    live_validation_status: str = "NOT_RUN"
    required_configuration: str


class EmailRuntimeStatus(BaseModel):
    transport_status: str
    destination_status: str
    sending_status: str
    last_outcome: str | None = None


class SystemReadiness(BaseModel):
    current_program_id: str
    current_batch: str
    components: list[ReadinessComponent]
    social: list[SocialAccessDiagnostic]
    provider_policies: list[ProviderPolicy] = Field(default_factory=list)
    email: EmailRuntimeStatus


def system_readiness(
    runtime_settings: Settings = settings,
    *,
    registry: ProgramRegistry | None = None,
) -> SystemReadiness:
    selected_registry = registry or get_program_registry()
    programs = selected_registry.active()
    current = programs[0]
    report = SourceQualityReportStore(runtime_settings.source_quality_report_path).load()
    discovery_status = ReadinessStatus.READY
    discovery_summary = "Ready; no intelligence cycle has completed yet."
    technical = None
    if report is not None:
        technical = report.stop_reason
        if report.discovery_quality.unknown_hypotheses:
            discovery_status = ReadinessStatus.READY
            discovery_summary = (
                f"{report.discovery_quality.unknown_hypotheses} unknown hypotheses "
                "were produced in the latest bounded validation."
            )
        elif report.blocked_or_unavailable_sources:
            discovery_status = ReadinessStatus.DEGRADED
            discovery_summary = (
                "Web discovery is temporarily unavailable; healthy enrichment "
                "sources cannot substitute for discovery."
            )
        else:
            discovery_status = ReadinessStatus.READY
            discovery_summary = (
                "No actionable candidates appeared in the sources successfully checked."
            )
    email_service = EmailNotificationService(
        runtime_settings.email_config_path,
        smtp_host=runtime_settings.email_smtp_host,
        smtp_port=runtime_settings.email_smtp_port,
        smtp_username=runtime_settings.email_smtp_username,
        smtp_password=runtime_settings.email_smtp_password,
        smtp_from=runtime_settings.email_smtp_from,
    )
    email = EmailRuntimeStatus.model_validate(email_service.status())
    product_hunt_token = runtime_settings.product_hunt_access_token
    product_hunt_configured = bool(
        runtime_settings.product_hunt_api_enabled
        and product_hunt_token
        and product_hunt_token.get_secret_value().strip()
    )
    github_token = runtime_settings.github_token
    github_configured = bool(github_token and github_token.get_secret_value().strip())
    discord_token = runtime_settings.discord_bot_token
    discord_configured = bool(
        runtime_settings.discord_api_enabled
        and discord_token
        and discord_token.get_secret_value().strip()
        and (
            runtime_settings.discord_authorized_guild_ids.strip()
            or runtime_settings.discord_authorized_channel_ids.strip()
        )
    )
    firecrawl_configured = bool(
        runtime_settings.firecrawl_api_url and runtime_settings.firecrawl_api_key
    )
    scheduler = OperationsStateStore(runtime_settings.operations_state_path).scheduler()
    try:
        Database(runtime_settings.db_path)
        database_status = ReadinessStatus.READY
        database_summary = "SQLite is accessible and migrations are current."
    except Exception as error:  # pragma: no cover - environment-specific failure
        database_status = ReadinessStatus.BROKEN
        database_summary = f"Database unavailable: {str(error)[:160]}"
    tool_diagnostics = local_osint_tool_diagnostics()
    installed_tools = [item["tool"] for item in tool_diagnostics if item["installed"]]
    return SystemReadiness(
        current_program_id=current.id,
        current_batch=current.batch,
        components=[
            ReadinessComponent(
                component="Discovery",
                status=discovery_status,
                summary=discovery_summary,
                action="Run bounded source checks or retry discovery.",
                technical_status=technical,
            ),
            ReadinessComponent(
                component="Verification",
                status=ReadinessStatus.READY,
                summary="Directory and public-page evidence remain verification-only.",
            ),
            ReadinessComponent(
                component="Enrichment",
                status=ReadinessStatus.READY,
                summary="Public technical and publication adapters are available when relevant.",
            ),
            ReadinessComponent(
                component="Indexed Social",
                status=(
                    ReadinessStatus.DEGRADED
                    if report and report.blocked_or_unavailable_sources
                    else ReadinessStatus.READY
                ),
                summary="Indexed discovery is permitted; direct social crawling is disabled.",
                action="Use an approved API, permitted index, or manual evidence import.",
            ),
            ReadinessComponent(
                component="Analyst",
                status=ReadinessStatus.READY,
                summary=(
                    "Local Ollama/Qwen is selected; runtime reachability requires doctor."
                    if runtime_settings.analyst_enabled
                    and runtime_settings.analyst_provider.casefold() in {"qwen", "auto"}
                    else (
                        "Deterministic grounded analysis is available; local Ollama/Qwen "
                        "is optional."
                    )
                ),
                action=(
                    "Verify the configured Ollama model with doctor."
                    if runtime_settings.analyst_enabled
                    and runtime_settings.analyst_provider.casefold() in {"qwen", "auto"}
                    else "Deterministic analyst is available without model configuration."
                ),
            ),
            ReadinessComponent(
                component="Optional Local OSINT",
                status=(
                    ReadinessStatus.READY
                    if installed_tools
                    else ReadinessStatus.OPTIONAL_NOT_CONFIGURED
                ),
                summary=(
                    f"Detected: {', '.join(installed_tools)}. Username matches remain clues only."
                    if installed_tools
                    else "Maigret and Sherlock are optional and were not detected."
                ),
                action="Install optional tools only when public-profile corroboration is useful.",
            ),
            ReadinessComponent(
                component="Product Hunt",
                status=(
                    ReadinessStatus.READY
                    if product_hunt_configured
                    else (
                        ReadinessStatus.USER_ACTION_REQUIRED
                        if runtime_settings.product_hunt_api_enabled
                        else ReadinessStatus.OPTIONAL_NOT_CONFIGURED
                    )
                ),
                summary=(
                    "Official read-only API authentication is configured."
                    if product_hunt_configured
                    else "Optional official Product Hunt API is not configured."
                ),
                action="Configure API enablement and access token only when needed.",
            ),
            ReadinessComponent(
                component="GitHub Authentication",
                status=(
                    ReadinessStatus.READY
                    if github_configured
                    else ReadinessStatus.OPTIONAL_NOT_CONFIGURED
                ),
                summary=(
                    "Optional read-only API authentication is configured."
                    if github_configured
                    else "Public GitHub access remains available without a token."
                ),
            ),
            ReadinessComponent(
                component="Discord Authorized API",
                status=(
                    ReadinessStatus.READY
                    if discord_configured
                    else (
                        ReadinessStatus.USER_ACTION_REQUIRED
                        if runtime_settings.discord_api_enabled
                        else ReadinessStatus.OPTIONAL_NOT_CONFIGURED
                    )
                ),
                summary=(
                    "Authorized guild/channel API boundary is configured."
                    if discord_configured
                    else "Optional authorized Discord API is disabled."
                ),
            ),
            ReadinessComponent(
                component="Firecrawl",
                status=(
                    ReadinessStatus.READY
                    if firecrawl_configured
                    else ReadinessStatus.OPTIONAL_NOT_CONFIGURED
                ),
                summary=(
                    "Optional Firecrawl boundary is configured."
                    if firecrawl_configured
                    else "Optional Firecrawl integration is disabled."
                ),
            ),
            ReadinessComponent(
                component="Email Delivery",
                status=(
                    ReadinessStatus.READY
                    if email.sending_status == "ENABLED"
                    else ReadinessStatus.OPTIONAL_NOT_CONFIGURED
                ),
                summary=(
                    "SMTP transport, destination, and sending are configured."
                    if email.sending_status == "ENABLED"
                    else (
                        "SMTP transport is configured; no destination is configured, "
                        "so sending is disabled."
                        if email.transport_status == "CONFIGURED"
                        and email.destination_status == "NOT_CONFIGURED"
                        else "SMTP transport is not configured; preview remains available."
                    )
                ),
                action="Add a destination only when delivery is wanted.",
            ),
            ReadinessComponent(
                component="Scheduler",
                status=(
                    ReadinessStatus.READY
                    if scheduler.enabled
                    else ReadinessStatus.OPTIONAL_NOT_CONFIGURED
                ),
                summary=(
                    f"Cycle operations enabled; configured cadence is "
                    f"{scheduler.interval_hours:g} hours."
                    if scheduler.enabled
                    else "Scheduler is disabled."
                ),
                action=(
                    "For unattended operation, schedule 'venture-intelligence --json "
                    "cycle run' with the operating system; use scheduler start only "
                    "for a foreground local process."
                ),
            ),
            ReadinessComponent(
                component="Database",
                status=database_status,
                summary=database_summary,
            ),
        ],
        email=email,
        social=[
            SocialAccessDiagnostic(
                platform=platform,
                indexed_discovery_available=True,
                required_configuration=(
                    "Authorized platform API or explicit operator permission for direct access; "
                    "otherwise indexed/manual evidence only."
                ),
                live_validation_status=(
                    "INDEXED_DEGRADED"
                    if report and report.blocked_or_unavailable_sources
                    else "NOT_RUN"
                ),
            )
            for platform in ("LinkedIn", "X", "Instagram")
        ],
        provider_policies=[
            *BUILTIN_PROVIDER_POLICIES,
            *CONNECTOR_POLICIES,
            *OPTIONAL_INTEGRATION_POLICIES,
        ],
    )


__all__ = [
    "EmailRuntimeStatus",
    "ReadinessComponent",
    "ReadinessStatus",
    "SocialAccessDiagnostic",
    "SystemReadiness",
    "system_readiness",
]
