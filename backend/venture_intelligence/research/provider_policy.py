"""Typed public-source capabilities and normalized operation outcomes.

Provider-specific transports stop at this boundary.  Downstream research code
receives JSON-native results plus a truthful, shared status vocabulary; it does
not infer that discovery evidence is identity verification.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from venture_intelligence.shared.models import (
    ResearchQueryIntent,
    ResearchSourceStatus,
    SourceAccessMode,
    SourceOperationStatus,
)


class ProviderCapability(str, Enum):
    DISCOVERY = "discovery"
    IDENTITY = "identity"
    PROGRAM = "program"
    TECHNICAL = "technical"
    PUBLICATION = "publication"
    COMPANY_ACTIVITY = "company_activity"
    SOCIAL_ANNOUNCEMENT = "social_announcement"
    HISTORICAL = "historical"
    FUNDING = "funding"
    INVESTOR_SIGNAL = "investor_signal"
    MARKET_SIGNAL = "market_signal"
    TEMPORAL_NEWS = "temporal_news"


class SourceAccessMethod(str, Enum):
    PUBLIC_API = "public_api"
    PUBLIC_DATASET = "public_dataset"
    RSS = "rss"
    PUBLIC_HTML = "public_html"
    SEARCH_DISCOVERY = "search_discovery"
    RENDERED_PUBLIC = "rendered_public"
    MANUAL_IMPORT = "manual_import"
    LOCAL_TOOL = "local_tool"


class EvidenceRole(str, Enum):
    DISCOVERY = "discovery"
    VERIFICATION = "verification"
    BOTH = "both"


class ProviderPolicy(BaseModel):
    """Safe, non-secret metadata describing one acquisition boundary."""

    provider_id: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    capabilities: list[ProviderCapability]
    access_method: SourceAccessMethod
    access_mode: SourceAccessMode
    evidence_role: EvidenceRole
    accepted_query_intents: list[ResearchQueryIntent] = Field(
        default_factory=lambda: [ResearchQueryIntent.WEB_DISCOVERY]
    )
    default_enabled: bool = True
    requires_credentials: bool = False
    live_test_supported: bool = False
    restricted_hosts: list[str] = Field(default_factory=list)
    rate_limit_note: str | None = None
    fallback: str | None = None
    parser_version: str = "1"
    configuration_requirements: list[str] = Field(default_factory=list)


def normalize_source_operation_status(
    status: ResearchSourceStatus,
    *,
    has_results: bool,
    error: str | None = None,
) -> SourceOperationStatus:
    """Normalize legacy provider status while preserving partial evidence."""
    if has_results:
        return (
            SourceOperationStatus.SUCCESS
            if status is ResearchSourceStatus.AVAILABLE
            else SourceOperationStatus.PARTIAL
        )
    if status is ResearchSourceStatus.AVAILABLE:
        return SourceOperationStatus.EMPTY

    message = (error or "").casefold()
    if "circuit" in message and ("open" in message or "blocked" in message):
        return SourceOperationStatus.CIRCUIT_OPEN
    if "timeout" in message or "timed out" in message:
        return SourceOperationStatus.TIMEOUT
    if "permission" in message:
        return SourceOperationStatus.PERMISSION_REQUIRED
    if "parse" in message or "malformed" in message:
        return SourceOperationStatus.PARSER_ERROR
    if any(token in message for token in ("dns", "network", "connection")):
        return SourceOperationStatus.NETWORK_ERROR

    return {
        ResearchSourceStatus.SEARCH_DISCOVERABLE: SourceOperationStatus.EMPTY,
        ResearchSourceStatus.AUTH_REQUIRED: SourceOperationStatus.AUTH_REQUIRED,
        ResearchSourceStatus.BLOCKED: SourceOperationStatus.BLOCKED,
        ResearchSourceStatus.RATE_LIMITED: SourceOperationStatus.RATE_LIMITED,
        ResearchSourceStatus.TEMPORARILY_UNAVAILABLE: SourceOperationStatus.TEMPORARILY_UNAVAILABLE,
        ResearchSourceStatus.UNAVAILABLE: SourceOperationStatus.UNAVAILABLE,
        ResearchSourceStatus.FAILED: SourceOperationStatus.FAILED,
        ResearchSourceStatus.BUDGET_EXHAUSTED: SourceOperationStatus.BUDGET_EXHAUSTED,
    }[status]


DUCKDUCKGO_POLICY = ProviderPolicy(
    provider_id="duckduckgo",
    display_name="DuckDuckGo public HTML discovery",
    capabilities=[ProviderCapability.DISCOVERY],
    access_method=SourceAccessMethod.SEARCH_DISCOVERY,
    access_mode=SourceAccessMode.SEARCH_DISCOVERY,
    evidence_role=EvidenceRole.DISCOVERY,
    accepted_query_intents=list(ResearchQueryIntent),
    live_test_supported=True,
    rate_limit_note="Best effort; 403/429 responses are structured failures.",
)

BING_POLICY = ProviderPolicy(
    provider_id="bing",
    display_name="Bing public HTML discovery",
    capabilities=[ProviderCapability.DISCOVERY],
    access_method=SourceAccessMethod.SEARCH_DISCOVERY,
    access_mode=SourceAccessMode.SEARCH_DISCOVERY,
    evidence_role=EvidenceRole.DISCOVERY,
    accepted_query_intents=list(ResearchQueryIntent),
    live_test_supported=True,
    rate_limit_note="Best effort public result pages; no login or API key required.",
)

YAHOO_POLICY = ProviderPolicy(
    provider_id="yahoo",
    display_name="Yahoo public HTML discovery",
    capabilities=[ProviderCapability.DISCOVERY],
    access_method=SourceAccessMethod.SEARCH_DISCOVERY,
    access_mode=SourceAccessMode.SEARCH_DISCOVERY,
    evidence_role=EvidenceRole.DISCOVERY,
    accepted_query_intents=list(ResearchQueryIntent),
    live_test_supported=True,
    rate_limit_note="Best effort public result pages; no login or API key required.",
)

BRAVE_POLICY = ProviderPolicy(
    provider_id="brave",
    display_name="Brave public HTML discovery",
    capabilities=[ProviderCapability.DISCOVERY],
    access_method=SourceAccessMethod.SEARCH_DISCOVERY,
    access_mode=SourceAccessMode.SEARCH_DISCOVERY,
    evidence_role=EvidenceRole.DISCOVERY,
    accepted_query_intents=list(ResearchQueryIntent),
    live_test_supported=True,
    rate_limit_note="Best effort public result pages; no paid Search API required.",
)

PUBLIC_PROGRAM_OSINT_POLICY = ProviderPolicy(
    provider_id="public_program_osint",
    display_name="Indexed public program announcements",
    capabilities=[
        ProviderCapability.DISCOVERY,
        ProviderCapability.PROGRAM,
        ProviderCapability.SOCIAL_ANNOUNCEMENT,
    ],
    access_method=SourceAccessMethod.SEARCH_DISCOVERY,
    access_mode=SourceAccessMode.SEARCH_DISCOVERY,
    evidence_role=EvidenceRole.DISCOVERY,
    accepted_query_intents=[ResearchQueryIntent.WEB_DISCOVERY],
    live_test_supported=True,
    restricted_hosts=[
        "linkedin.com",
        "x.com",
        "twitter.com",
        "instagram.com",
        "threads.net",
    ],
    fallback="Search-index evidence only; direct social access remains disabled.",
)

ARXIV_POLICY = ProviderPolicy(
    provider_id="arxiv",
    display_name="arXiv official API",
    capabilities=[ProviderCapability.PUBLICATION],
    access_method=SourceAccessMethod.PUBLIC_API,
    access_mode=SourceAccessMode.APPROVED_API,
    evidence_role=EvidenceRole.BOTH,
    accepted_query_intents=[ResearchQueryIntent.PUBLICATION],
    live_test_supported=True,
)

GOOGLE_SCHOLAR_POLICY = ProviderPolicy(
    provider_id="google_scholar",
    display_name="Google Scholar public discovery",
    capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.PUBLICATION],
    access_method=SourceAccessMethod.PUBLIC_HTML,
    access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
    evidence_role=EvidenceRole.DISCOVERY,
    accepted_query_intents=[ResearchQueryIntent.PUBLICATION],
    default_enabled=False,
    live_test_supported=False,
    rate_limit_note="Optional and failure-tolerant; no anti-bot circumvention.",
    fallback="Use arXiv and ordinary public-web discovery.",
)

WAYBACK_POLICY = ProviderPolicy(
    provider_id="wayback",
    display_name="Internet Archive CDX API",
    capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.HISTORICAL],
    access_method=SourceAccessMethod.PUBLIC_API,
    access_mode=SourceAccessMode.APPROVED_API,
    evidence_role=EvidenceRole.BOTH,
    accepted_query_intents=[ResearchQueryIntent.HISTORICAL],
    live_test_supported=True,
)

RSS_POLICY = ProviderPolicy(
    provider_id="rss",
    display_name="Configured public RSS/Atom feeds",
    capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.COMPANY_ACTIVITY],
    access_method=SourceAccessMethod.RSS,
    access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
    evidence_role=EvidenceRole.BOTH,
    accepted_query_intents=[ResearchQueryIntent.WEB_DISCOVERY],
    live_test_supported=True,
)

TECHCRUNCH_POLICY = ProviderPolicy(
    provider_id="techcrunch",
    display_name="TechCrunch public RSS",
    capabilities=[
        ProviderCapability.DISCOVERY,
        ProviderCapability.FUNDING,
        ProviderCapability.IDENTITY,
        ProviderCapability.MARKET_SIGNAL,
        ProviderCapability.TEMPORAL_NEWS,
    ],
    access_method=SourceAccessMethod.RSS,
    access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
    evidence_role=EvidenceRole.BOTH,
    accepted_query_intents=[ResearchQueryIntent.WEB_DISCOVERY],
    live_test_supported=True,
)

NEWSLETTER_POLICY = ProviderPolicy(
    provider_id="newsletter_watchlist",
    display_name="Configured public newsletter and Substack feeds",
    capabilities=[
        ProviderCapability.DISCOVERY,
        ProviderCapability.COMPANY_ACTIVITY,
        ProviderCapability.FUNDING,
        ProviderCapability.INVESTOR_SIGNAL,
        ProviderCapability.TEMPORAL_NEWS,
    ],
    access_method=SourceAccessMethod.RSS,
    access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
    evidence_role=EvidenceRole.BOTH,
    accepted_query_intents=[ResearchQueryIntent.WEB_DISCOVERY],
    live_test_supported=True,
)

VC_WATCHLIST_POLICY = ProviderPolicy(
    provider_id="vc_watchlist",
    display_name="Configured public VC and partner pages",
    capabilities=[
        ProviderCapability.DISCOVERY,
        ProviderCapability.FUNDING,
        ProviderCapability.INVESTOR_SIGNAL,
        ProviderCapability.TEMPORAL_NEWS,
    ],
    access_method=SourceAccessMethod.PUBLIC_HTML,
    access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
    evidence_role=EvidenceRole.BOTH,
    accepted_query_intents=[ResearchQueryIntent.WEB_DISCOVERY],
    live_test_supported=True,
)

BUILTIN_PROVIDER_POLICIES = (
    DUCKDUCKGO_POLICY,
    BING_POLICY,
    YAHOO_POLICY,
    BRAVE_POLICY,
    PUBLIC_PROGRAM_OSINT_POLICY,
    ARXIV_POLICY,
    GOOGLE_SCHOLAR_POLICY,
    WAYBACK_POLICY,
    RSS_POLICY,
    TECHCRUNCH_POLICY,
    NEWSLETTER_POLICY,
    VC_WATCHLIST_POLICY,
)


CONNECTOR_POLICIES = (
    ProviderPolicy(
        provider_id="directory",
        display_name="Configured accelerator directory connector",
        capabilities=[ProviderCapability.IDENTITY, ProviderCapability.PROGRAM],
        access_method=SourceAccessMethod.PUBLIC_HTML,
        access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
        evidence_role=EvidenceRole.VERIFICATION,
    ),
    ProviderPolicy(
        provider_id="triangulation",
        display_name="Configured public-source triangulation connector",
        capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.PROGRAM],
        access_method=SourceAccessMethod.PUBLIC_HTML,
        access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
        evidence_role=EvidenceRole.BOTH,
    ),
    ProviderPolicy(
        provider_id="newsletter",
        display_name="Configured public newsletter connector",
        capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.COMPANY_ACTIVITY],
        access_method=SourceAccessMethod.RSS,
        access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
        evidence_role=EvidenceRole.BOTH,
    ),
    ProviderPolicy(
        provider_id="blog",
        display_name="Configured public blog connector",
        capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.COMPANY_ACTIVITY],
        access_method=SourceAccessMethod.PUBLIC_HTML,
        access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
        evidence_role=EvidenceRole.BOTH,
    ),
    ProviderPolicy(
        provider_id="website",
        display_name="Configured official website connector",
        capabilities=[ProviderCapability.IDENTITY, ProviderCapability.COMPANY_ACTIVITY],
        access_method=SourceAccessMethod.PUBLIC_HTML,
        access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
        evidence_role=EvidenceRole.VERIFICATION,
    ),
    ProviderPolicy(
        provider_id="social",
        display_name="Indexed social announcement connector",
        capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.SOCIAL_ANNOUNCEMENT],
        access_method=SourceAccessMethod.SEARCH_DISCOVERY,
        access_mode=SourceAccessMode.SEARCH_DISCOVERY,
        evidence_role=EvidenceRole.DISCOVERY,
        restricted_hosts=[
            "linkedin.com",
            "x.com",
            "twitter.com",
            "instagram.com",
            "threads.net",
        ],
        fallback="Direct social access is disabled unless explicitly approved.",
    ),
    ProviderPolicy(
        provider_id="discord",
        display_name="Discord public widget connector",
        capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.SOCIAL_ANNOUNCEMENT],
        access_method=SourceAccessMethod.PUBLIC_API,
        access_mode=SourceAccessMode.APPROVED_API,
        evidence_role=EvidenceRole.DISCOVERY,
        fallback="Public server-owner-enabled widgets only.",
    ),
    ProviderPolicy(
        provider_id="linkedin",
        display_name="LinkedIn direct-site connector",
        capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.IDENTITY],
        access_method=SourceAccessMethod.PUBLIC_HTML,
        access_mode=SourceAccessMode.PERMISSION_REQUIRED,
        evidence_role=EvidenceRole.DISCOVERY,
        default_enabled=False,
        restricted_hosts=["linkedin.com"],
        fallback="Use permitted search-provider indexed discovery.",
    ),
    ProviderPolicy(
        provider_id="github",
        display_name="GitHub public API connector",
        capabilities=[ProviderCapability.IDENTITY, ProviderCapability.TECHNICAL],
        access_method=SourceAccessMethod.PUBLIC_API,
        access_mode=SourceAccessMode.APPROVED_API,
        evidence_role=EvidenceRole.BOTH,
        accepted_query_intents=[ResearchQueryIntent.TECHNICAL],
    ),
    ProviderPolicy(
        provider_id="hacker_news",
        display_name="Hacker News Algolia API connector",
        capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.COMPANY_ACTIVITY],
        access_method=SourceAccessMethod.PUBLIC_API,
        access_mode=SourceAccessMode.APPROVED_API,
        evidence_role=EvidenceRole.BOTH,
    ),
    ProviderPolicy(
        provider_id="arxiv",
        display_name="arXiv public API connector",
        capabilities=[ProviderCapability.PUBLICATION],
        access_method=SourceAccessMethod.PUBLIC_API,
        access_mode=SourceAccessMode.APPROVED_API,
        evidence_role=EvidenceRole.BOTH,
        accepted_query_intents=[ResearchQueryIntent.PUBLICATION],
    ),
    ProviderPolicy(
        provider_id="product_hunt",
        display_name="Product Hunt official GraphQL connector",
        capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.COMPANY_ACTIVITY],
        access_method=SourceAccessMethod.PUBLIC_API,
        access_mode=SourceAccessMode.PERMISSION_REQUIRED,
        evidence_role=EvidenceRole.DISCOVERY,
        default_enabled=False,
        requires_credentials=True,
        fallback="Use permitted indexed or archived Product Hunt references.",
        configuration_requirements=[
            "PRODUCT_HUNT_ACCESS_TOKEN",
            "PRODUCT_HUNT_API_ENABLED=true",
        ],
        rate_limit_note=(
            "Operators remain responsible for Product Hunt API terms and any "
            "deployment-specific permissions."
        ),
    ),
    ProviderPolicy(
        provider_id="wellfound",
        display_name="Wellfound public-web connector",
        capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.COMPANY_ACTIVITY],
        access_method=SourceAccessMethod.PUBLIC_HTML,
        access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
        evidence_role=EvidenceRole.DISCOVERY,
        fallback="Blocked or login-required responses yield no evidence.",
    ),
)


OPTIONAL_INTEGRATION_POLICIES = (
    ProviderPolicy(
        provider_id="discord_authorized_api",
        display_name="Optional authorized Discord official API boundary",
        capabilities=[ProviderCapability.DISCOVERY, ProviderCapability.SOCIAL_ANNOUNCEMENT],
        access_method=SourceAccessMethod.PUBLIC_API,
        access_mode=SourceAccessMode.PERMISSION_REQUIRED,
        evidence_role=EvidenceRole.DISCOVERY,
        default_enabled=False,
        requires_credentials=True,
        fallback="Use the public server-owner-enabled widget when available.",
        configuration_requirements=[
            "DISCORD_API_ENABLED=true",
            "DISCORD_BOT_TOKEN",
            "DISCORD_AUTHORIZED_GUILD_IDS and/or DISCORD_AUTHORIZED_CHANNEL_IDS",
        ],
        rate_limit_note=(
            "Boundary only: read-only least-privilege access is limited to explicitly "
            "operator-authorized guilds/channels; no self-bots or user tokens."
        ),
    ),
    ProviderPolicy(
        provider_id="firecrawl",
        display_name="Optional Firecrawl API boundary",
        capabilities=[ProviderCapability.DISCOVERY],
        access_method=SourceAccessMethod.PUBLIC_API,
        access_mode=SourceAccessMode.APPROVED_API,
        evidence_role=EvidenceRole.DISCOVERY,
        default_enabled=False,
        requires_credentials=True,
        fallback="Use the built-in bounded public-web crawler.",
        configuration_requirements=["Firecrawl endpoint and credential"],
    ),
    ProviderPolicy(
        provider_id="maigret",
        display_name="Bounded local Maigret username adapter",
        capabilities=[ProviderCapability.IDENTITY],
        access_method=SourceAccessMethod.LOCAL_TOOL,
        access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
        evidence_role=EvidenceRole.DISCOVERY,
        default_enabled=False,
        live_test_supported=True,
        fallback="Manual JSON import remains available when the executable is absent.",
        configuration_requirements=["maigret executable on PATH"],
    ),
    ProviderPolicy(
        provider_id="sherlock",
        display_name="Bounded local Sherlock username adapter",
        capabilities=[ProviderCapability.IDENTITY],
        access_method=SourceAccessMethod.LOCAL_TOOL,
        access_mode=SourceAccessMode.APPROVED_PUBLIC_WEB,
        evidence_role=EvidenceRole.DISCOVERY,
        default_enabled=False,
        live_test_supported=True,
        fallback="Manual JSON import remains available when the executable is absent.",
        configuration_requirements=["sherlock executable on PATH"],
    ),
)
