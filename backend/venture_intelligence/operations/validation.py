"""Policy-gated, read-only validation against bounded public sources."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

from venture_intelligence.batch_discovery.engine import stable_digest
from venture_intelligence.batch_discovery.evaluation import evaluate_discovery
from venture_intelligence.batch_discovery.models import BatchDiscoveryRequest
from venture_intelligence.batch_discovery.repository import BatchDiscoveryRepository
from venture_intelligence.batch_discovery.service import BatchDiscoveryService
from venture_intelligence.batch_discovery.tools import (
    deduplicate_public_profile_clues,
    public_profile_origin,
    run_local_osint_tool,
)
from venture_intelligence.config import settings
from venture_intelligence.connectors.public_sources import ProductHuntConnector
from venture_intelligence.crawler import StealthCrawler
from venture_intelligence.operations.inventory import build_connector_inventory
from venture_intelligence.operations.models import (
    DedicatedOsintToolDiagnostic,
    LiveCandidateDiagnostic,
    LiveDiscoveryQuality,
    LiveSourceDiagnostic,
    OperationalValidationReport,
    SocialPlatformDiagnostic,
    SourceQualityState,
)
from venture_intelligence.operations.store import SourceQualityReportStore
from venture_intelligence.programs import ProgramRegistry, get_program_registry
from venture_intelligence.research.provider_policy import (
    NEWSLETTER_POLICY,
    TECHCRUNCH_POLICY,
    normalize_source_operation_status,
)
from venture_intelligence.research.request_budget import RequestBudgetTracker
from venture_intelligence.research.sources import (
    ArxivSearchProvider,
    BingSearchProvider,
    BraveSearchProvider,
    DuckDuckGoSearchProvider,
    PublicProgramAnnouncementProvider,
    PublicWatchlistPageProvider,
    RssSearchProvider,
    SearchResponse,
    WaybackSearchProvider,
    YahooSearchProvider,
)
from venture_intelligence.research.watchlist import (
    IntelligenceWatchlist,
    SourceStateStore,
)
from venture_intelligence.shared.models import (
    ResearchBudget,
    ResearchSourceStatus,
    SourceOperationStatus,
)
from venture_intelligence.storage.db import Database

_LIVE_GATE = "VENTURE_INTELLIGENCE_LIVE_TESTS"
_RESTRICTED_SOCIAL = {
    "linkedin.com",
    "x.com",
    "twitter.com",
    "instagram.com",
    "threads.net",
}
_BLOCKED = {
    SourceOperationStatus.BLOCKED,
    SourceOperationStatus.AUTH_REQUIRED,
    SourceOperationStatus.PERMISSION_REQUIRED,
    SourceOperationStatus.RATE_LIMITED,
    SourceOperationStatus.CIRCUIT_OPEN,
}
_UNAVAILABLE = {
    SourceOperationStatus.TIMEOUT,
    SourceOperationStatus.NETWORK_ERROR,
    SourceOperationStatus.TEMPORARILY_UNAVAILABLE,
    SourceOperationStatus.UNAVAILABLE,
    SourceOperationStatus.FAILED,
}


def require_live_gate(*, allow_network: bool) -> None:
    if not allow_network or os.getenv(_LIVE_GATE) != "1":
        raise PermissionError(
            "live validation requires VENTURE_INTELLIGENCE_LIVE_TESTS=1 "
            "and the explicit --allow-network flag"
        )


def bounded_live_budget(*, max_queries: int = 8, max_requests: int = 24) -> ResearchBudget:
    requests = max(1, min(max_requests, 24))
    return ResearchBudget(
        max_queries=max(1, min(max_queries, 8)),
        max_depth=1,
        max_results_per_query=3,
        max_runtime_seconds=90,
        max_total_requests=requests,
        max_requests_per_provider=min(12, requests),
        max_retry_requests=min(3, requests),
        max_requests_per_domain=min(12, requests),
    )


def _state(status: SourceOperationStatus, *, results: int, drift: str | None) -> SourceQualityState:
    if drift:
        return SourceQualityState.DRIFTED
    if status in _BLOCKED:
        return SourceQualityState.BLOCKED
    if status in _UNAVAILABLE:
        return SourceQualityState.UNAVAILABLE
    if status is SourceOperationStatus.BUDGET_EXHAUSTED:
        return SourceQualityState.NOT_RUN
    if status is SourceOperationStatus.EMPTY or results == 0:
        return SourceQualityState.EMPTY
    if status is SourceOperationStatus.SUCCESS:
        return SourceQualityState.HEALTHY
    return SourceQualityState.DEGRADED


def _diagnostic(
    source_id: str,
    response: SearchResponse,
    *,
    role: str,
    latency_ms: float,
    validation_scope: str = "normal_live_discovery",
    known_positive_control: bool = False,
) -> LiveSourceDiagnostic:
    status = response.operation_status or SourceOperationStatus.FAILED
    drift = (
        "provider returned a parser error" if status is SourceOperationStatus.PARSER_ERROR else None
    )
    result_hosts = {
        (urlsplit(item.url).hostname or "").casefold().removeprefix("www.")
        for item in response.results
    }
    hosts = sorted(
        restricted
        for restricted in _RESTRICTED_SOCIAL
        if any(host == restricted or host.endswith(f".{restricted}") for host in result_hosts)
    )
    return LiveSourceDiagnostic(
        source_id=source_id,
        state=_state(status, results=len(response.results), drift=drift),
        operation_status=status,
        discovery_role=role,
        validation_scope=validation_scope,
        known_positive_control=known_positive_control,
        requests=response.attempts,
        retries=response.retry_requests,
        redirects=response.redirect_requests,
        results=len(response.results),
        unique_origins=len({item.url for item in response.results}),
        latency_ms=latency_ms,
        indexed_social_hosts=hosts,
        final_urls=[item.url for item in response.results[:5]],
        http_status=response.http_status,
        error=response.error,
        drift_reason=drift,
        retryable=response.retryable,
    )


async def _search_probe(
    source_id: str,
    provider,
    query: str,
    *,
    role: str,
    validation_scope: str = "normal_live_discovery",
    known_positive_control: bool = False,
    expected_hosts: set[str] | None = None,
) -> LiveSourceDiagnostic:
    started = time.monotonic()
    try:
        response = await provider.search(query, 3)
    except Exception as error:  # provider failures are report data, not runner failures
        response = SearchResponse(
            query=query,
            status="FAILED",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            error=f"{error.__class__.__name__}: {str(error)[:400]}",
        )
    diagnostic = _diagnostic(
        source_id,
        response,
        role=role,
        latency_ms=(time.monotonic() - started) * 1_000,
        validation_scope=validation_scope,
        known_positive_control=known_positive_control,
    )
    if expected_hosts:
        returned_hosts = {
            (urlsplit(item.url).hostname or "").casefold().removeprefix("www.")
            for item in response.results
        }
        matched = any(
            host == expected or host.endswith(f".{expected}")
            for host in returned_hosts
            for expected in expected_hosts
        )
        if response.results and not matched:
            diagnostic.state = SourceQualityState.DRIFTED
            diagnostic.operation_status = SourceOperationStatus.PARSER_ERROR
            diagnostic.drift_reason = (
                "known-positive indexed-social query returned results, but none matched "
                f"the required hosts: {', '.join(sorted(expected_hosts))}"
            )
            diagnostic.error = diagnostic.drift_reason
    return diagnostic


async def _github_probe(
    crawler: StealthCrawler,
    tracker: RequestBudgetTracker,
    query: str,
    *,
    source_id: str = "github",
    known_positive_control: bool = False,
) -> LiveSourceDiagnostic:
    url = f"https://api.github.com/search/repositories?q={quote_plus(query)}&per_page=3"
    started = time.monotonic()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    github_token = settings.github_token
    if github_token and github_token.get_secret_value().strip():
        headers["Authorization"] = f"Bearer {github_token.get_secret_value()}"
    result = await crawler.fetch_text_budgeted(
        url,
        tracker,
        "github-live-validation",
        headers=headers,
    )
    count = 0
    final_urls: list[str] = []
    drift = None
    if result.ok and result.text:
        try:
            payload = json.loads(result.text)
            items = payload.get("items", []) if isinstance(payload, dict) else []
            count = len(items) if isinstance(items, list) else 0
            final_urls = [
                str(item.get("html_url"))
                for item in items[:3]
                if isinstance(item, dict) and item.get("html_url")
            ]
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            drift = f"invalid GitHub JSON: {error}"
    status = (
        SourceOperationStatus.PARSER_ERROR
        if drift
        else (
            SourceOperationStatus.SUCCESS
            if result.ok and count
            else normalize_source_operation_status(
                result.source_status or ResearchSourceStatus.FAILED,
                has_results=False,
                error=result.error,
            )
        )
    )
    return LiveSourceDiagnostic(
        source_id=source_id,
        state=_state(status, results=count, drift=drift),
        operation_status=status,
        discovery_role="enrichment",
        validation_scope=(
            "known_positive_control" if known_positive_control else "normal_live_discovery"
        ),
        known_positive_control=known_positive_control,
        credential_configured=bool(github_token and github_token.get_secret_value().strip()),
        access_mode="official_public_api",
        requests=int(result.request_made),
        redirects=result.redirect_requests,
        results=count,
        unique_origins=len(set(final_urls)),
        latency_ms=(time.monotonic() - started) * 1_000,
        final_urls=final_urls,
        http_status=result.status_code,
        auth_state=(
            "configured"
            if github_token and github_token.get_secret_value().strip()
            else "optional_not_configured"
        ),
        rate_limit_limit=(
            int(result.headers["x-ratelimit-limit"])
            if result.headers.get("x-ratelimit-limit", "").isdigit()
            else None
        ),
        rate_limit_remaining=(
            int(result.headers["x-ratelimit-remaining"])
            if result.headers.get("x-ratelimit-remaining", "").isdigit()
            else None
        ),
        error=result.error,
        drift_reason=drift,
        retryable=status in _UNAVAILABLE,
    )


async def _json_api_probe(
    source_id: str,
    crawler: StealthCrawler,
    tracker: RequestBudgetTracker,
    *,
    url: str,
    result_key: str,
    role: str,
) -> LiveSourceDiagnostic:
    """Validate a stable public JSON API without turning its payload into entities."""
    started = time.monotonic()
    result = await crawler.fetch_text_budgeted(url, tracker, f"{source_id}-control")
    count = 0
    drift = None
    if result.ok and result.text:
        try:
            payload = json.loads(result.text)
            items = payload.get(result_key, []) if isinstance(payload, dict) else []
            if not isinstance(items, list):
                raise TypeError(f"{result_key} is not an array")
            count = len(items)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            drift = f"invalid {source_id} JSON: {error}"
    status = (
        SourceOperationStatus.PARSER_ERROR
        if drift
        else (
            SourceOperationStatus.SUCCESS
            if result.ok and count
            else normalize_source_operation_status(
                result.source_status or ResearchSourceStatus.FAILED,
                has_results=False,
                error=result.error,
            )
        )
    )
    return LiveSourceDiagnostic(
        source_id=source_id,
        state=_state(status, results=count, drift=drift),
        operation_status=status,
        discovery_role=role,
        validation_scope="known_positive_control",
        known_positive_control=True,
        access_mode="official_public_api",
        requests=int(result.request_made),
        redirects=result.redirect_requests,
        results=count,
        unique_origins=count,
        latency_ms=(time.monotonic() - started) * 1_000,
        final_urls=[result.url] if result.url else [],
        http_status=result.status_code,
        error=result.error,
        drift_reason=drift,
        retryable=status in _UNAVAILABLE,
    )


def _ground_truth(path: str | Path | None) -> dict[str, datetime]:
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ground truth must be an object mapping names to ISO timestamps")
    return {
        str(name): datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        for name, timestamp in payload.items()
    }


async def _product_hunt_probe(
    crawler: StealthCrawler,
    tracker: RequestBudgetTracker,
    registry: ProgramRegistry,
) -> LiveSourceDiagnostic:
    connector = ProductHuntConnector(registry, crawler, runtime_settings=settings)
    if not connector.configured:
        return LiveSourceDiagnostic(
            source_id="product_hunt:known-positive",
            state=SourceQualityState.NOT_RUN,
            operation_status=SourceOperationStatus.PERMISSION_REQUIRED,
            discovery_role="discovery",
            validation_scope="known_positive_control",
            known_positive_control=True,
            enabled=settings.product_hunt_api_enabled,
            configured=False,
            credential_configured=False,
            access_mode="authorized_official_api",
            auth_state="user_configuration_required",
            never_attempted_reason=(
                "Product Hunt API enablement and an access token are required"
            ),
        )
    connector.set_request_budget(tracker)
    started = time.monotonic()
    post, result = await connector.fetch_post(
        post_id=settings.product_hunt_known_positive_post_id
    )
    assert result is not None
    count = int(post is not None)
    status = normalize_source_operation_status(
        result.source_status or ResearchSourceStatus.FAILED,
        has_results=bool(count),
        error=result.error,
    )
    drift = None if count else "official API returned no usable known-positive post"
    return LiveSourceDiagnostic(
        source_id="product_hunt:known-positive",
        state=_state(status, results=count, drift=drift),
        operation_status=status,
        discovery_role="discovery",
        validation_scope="known_positive_control",
        known_positive_control=True,
        enabled=True,
        configured=True,
        credential_configured=True,
        access_mode="authorized_official_api",
        requests=int(result.request_made),
        redirects=result.redirect_requests,
        results=count,
        relevant_items=count,
        accepted_observations=count,
        unique_origins=count,
        latency_ms=(time.monotonic() - started) * 1_000,
        final_urls=[result.url] if result.url else [],
        http_status=result.status_code,
        auth_state="configured",
        error=result.error,
        drift_reason=drift,
        retryable=status in _UNAVAILABLE,
    )


async def run_live_validation(
    *,
    program_id: str,
    report_path: str | Path,
    allow_network: bool,
    ground_truth_path: str | Path | None = None,
    max_queries: int = 8,
    max_requests: int = 24,
    registry: ProgramRegistry | None = None,
) -> OperationalValidationReport:
    """Run live checks in a disposable store and persist only the audit report."""
    require_live_gate(allow_network=allow_network)
    active_registry = registry or get_program_registry()
    program = active_registry.get(program_id)
    if program is None or not program.active:
        raise LookupError(f"Unknown active program id: {program_id}")
    started_at = datetime.now(timezone.utc)
    budget = bounded_live_budget(max_queries=max_queries, max_requests=max_requests)
    osint_runs = [
        await run_local_osint_tool(
            tool,
            username="torvalds",
            allow_network=True,
            known_positive_expected=True,
        )
        for tool in ("maigret", "sherlock")
    ]
    osint_clues = deduplicate_public_profile_clues(osint_runs, username="torvalds")
    seen_osint_urls: set[str] = set()
    osint_diagnostics = []
    for tool_run in osint_runs:
        new_origins = 0
        cross_tool_duplicates = 0
        for result in tool_run.results:
            origin = public_profile_origin(result.url)
            if origin in seen_osint_urls:
                cross_tool_duplicates += 1
            else:
                seen_osint_urls.add(origin)
                new_origins += 1
        diagnostic = tool_run.model_dump(exclude={"results"})
        diagnostic["duplicates"] += cross_tool_duplicates
        diagnostic["observations_created"] = new_origins
        osint_diagnostics.append(diagnostic)
    discovery_budget = budget.model_copy(
        update={
            # One generic discovery intent plus one indexed-social intent. The
            # shared 24-request ceiling still reserves three bounded technical
            # probes after discovery.
            "max_queries": min(2, budget.max_queries),
            "max_total_requests": max(1, budget.max_total_requests - 3),
            "max_requests_per_provider": min(
                budget.max_requests_per_provider,
                max(1, budget.max_total_requests - 3),
            ),
            "max_requests_per_domain": min(
                budget.max_requests_per_domain,
                max(1, budget.max_total_requests - 3),
            ),
        }
    )
    diagnostics: list[LiveSourceDiagnostic] = []
    ground_truth = _ground_truth(ground_truth_path)

    with tempfile.TemporaryDirectory(prefix="venture-intelligence-m9-") as directory:
        repository = BatchDiscoveryRepository(Database(str(Path(directory) / "isolated.db")))
        crawler = StealthCrawler()
        watchlist = IntelligenceWatchlist.load(settings.intelligence_watchlist_path)
        state_store = SourceStateStore(Path(directory) / "source-state.json")
        techcrunch_entries = [
            item
            for item in watchlist.active("publication")
            if (item.organization or "").casefold() == "techcrunch"
        ]
        newsletter_entries = watchlist.active("newsletter")
        techcrunch_urls = [
            url for item in techcrunch_entries for url in item.feed_urls
        ]
        newsletter_urls = [
            url for item in newsletter_entries for url in item.feed_urls
        ]

        # Isolated known-positive controls run before the venture query. Their
        # budget and state cannot starve or contaminate the normal discovery run.
        control_budget = bounded_live_budget(max_queries=8, max_requests=24)
        control_tracker = RequestBudgetTracker(control_budget)
        control_crawler = StealthCrawler()
        diagnostics.append(
            await _product_hunt_probe(control_crawler, control_tracker, active_registry)
        )
        control_search_providers = [
            DuckDuckGoSearchProvider(control_crawler, max_retries=0),
            BingSearchProvider(control_crawler, max_retries=0),
            YahooSearchProvider(control_crawler, max_retries=0),
            BraveSearchProvider(control_crawler, max_retries=0),
        ]
        for provider in control_search_providers:
            provider.set_request_budget(control_tracker)
            provider_id = getattr(
                getattr(provider, "policy", None),
                "provider_id",
                provider.domain,
            )
            diagnostics.append(
                await _search_probe(
                    f"{provider_id}:known-positive",
                    provider,
                    '"Python Software Foundation"',
                    role="discovery",
                    validation_scope="known_positive_control",
                    known_positive_control=True,
                )
            )
        indexed_queries = {
            "linkedin": (
                'site:linkedin.com/in "Guillermo Rauch"',
                {"linkedin.com"},
            ),
            "x": ('site:x.com "Guillermo Rauch"', {"x.com", "twitter.com"}),
            "instagram": ("site:instagram.com nasa", {"instagram.com"}),
            "threads": ("site:threads.net nasa", {"threads.net"}),
        }
        for platform, (query, expected_hosts) in indexed_queries.items():
            for indexed_provider in control_search_providers[:3]:
                provider_id = getattr(
                    getattr(indexed_provider, "policy", None),
                    "provider_id",
                    indexed_provider.domain,
                )
                diagnostic = await _search_probe(
                    f"indexed_{platform}:{provider_id}:known-positive",
                    indexed_provider,
                    query,
                    role="discovery",
                    validation_scope="known_positive_control",
                    known_positive_control=True,
                    expected_hosts=expected_hosts,
                )
                diagnostics.append(diagnostic)
                if diagnostic.state is SourceQualityState.HEALTHY:
                    break
        control_arxiv = ArxivSearchProvider(max_retries=0, page_delay_seconds=0)
        control_arxiv.set_request_budget(control_tracker)
        diagnostics.append(
            await _search_probe(
                "arxiv:known-positive",
                control_arxiv,
                'all:"quantum computing"',
                role="enrichment",
                validation_scope="known_positive_control",
                known_positive_control=True,
            )
        )
        control_wayback = WaybackSearchProvider(control_crawler)
        control_wayback.set_request_budget(control_tracker)
        diagnostics.append(
            await _search_probe(
                "wayback:known-positive",
                control_wayback,
                "https://example.com",
                role="historical",
                validation_scope="known_positive_control",
                known_positive_control=True,
            )
        )
        diagnostics.append(
            await _github_probe(
                control_crawler,
                control_tracker,
                "repo:torvalds/linux",
                source_id="github:known-positive",
                known_positive_control=True,
            )
        )
        diagnostics.append(
            await _json_api_probe(
                "hacker_news:known-positive",
                control_crawler,
                control_tracker,
                url="https://hn.algolia.com/api/v1/search?query=python&hitsPerPage=3",
                result_key="hits",
                role="discovery",
            )
        )
        control_state_store = SourceStateStore(Path(directory) / "control-source-state.json")
        if techcrunch_urls:
            control_techcrunch = RssSearchProvider(
                techcrunch_urls,
                control_crawler,
                provider_id="techcrunch",
                policy=TECHCRUNCH_POLICY,
                state_store=control_state_store,
            )
            control_techcrunch.set_request_budget(control_tracker)
            diagnostics.append(
                await _search_probe(
                    "techcrunch:known-positive",
                    control_techcrunch,
                    "",
                    role="discovery",
                    validation_scope="known_positive_control",
                    known_positive_control=True,
                )
            )
        if newsletter_urls:
            control_newsletters = RssSearchProvider(
                newsletter_urls,
                control_crawler,
                provider_id="newsletter_watchlist",
                policy=NEWSLETTER_POLICY,
                state_store=control_state_store,
            )
            control_newsletters.set_request_budget(control_tracker)
            diagnostics.append(
                await _search_probe(
                    "newsletter_watchlist:known-positive",
                    control_newsletters,
                    "",
                    role="discovery",
                    validation_scope="known_positive_control",
                    known_positive_control=True,
                )
            )
        await control_arxiv.close()
        await control_crawler.close()

        providers = [
            DuckDuckGoSearchProvider(crawler, max_retries=1),
            BingSearchProvider(crawler, max_retries=1),
            YahooSearchProvider(crawler, max_retries=1),
            BraveSearchProvider(crawler, max_retries=1),
            PublicProgramAnnouncementProvider(
                crawler,
                program_terms=(program.name, program.batch),
                max_scoped_queries=1,
            ),
        ]
        techcrunch_provider: RssSearchProvider | None = None
        if techcrunch_urls:
            techcrunch_provider = RssSearchProvider(
                techcrunch_urls,
                crawler,
                provider_id="techcrunch",
                policy=TECHCRUNCH_POLICY,
                state_store=state_store,
                source_metadata={
                    url: item.model_dump()
                    for item in techcrunch_entries
                    for url in item.feed_urls
                },
            )
            providers.append(techcrunch_provider)
        if newsletter_urls:
            providers.append(
                RssSearchProvider(
                    newsletter_urls,
                    crawler,
                    provider_id="newsletter_watchlist",
                    policy=NEWSLETTER_POLICY,
                    state_store=state_store,
                    source_metadata={
                        url: item.model_dump()
                        for item in newsletter_entries
                        for url in item.feed_urls
                    },
                )
            )
        providers.append(
            PublicWatchlistPageProvider(
                watchlist.active("vc_firm", "vc_partner"),
                crawler,
                state_store=state_store,
            )
        )
        service = BatchDiscoveryService(
            repository,
            providers,
            crawler=crawler,
            registry=active_registry,
        )
        try:
            run = await service.run(
                BatchDiscoveryRequest(program_id=program.id, budget=discovery_budget),
                event_bus=None,
            )
            for outcome in run.source_outcomes:
                observations = [
                    item
                    for item in run.observations
                    if item.source_provider == outcome.provider_id
                    and item.provenance.get("discovery_stage") == outcome.stage.value
                ]
                social_hosts = sorted(
                    {item.source_domain for item in observations} & _RESTRICTED_SOCIAL
                )
                drift = (
                    "search parser returned no usable results after successful HTTP"
                    if outcome.status is SourceOperationStatus.PARSER_ERROR
                    else None
                )
                diagnostics.append(
                    LiveSourceDiagnostic(
                        source_id=(
                            f"{outcome.provider_id}:{outcome.source_family}:"
                            f"{outcome.stage.value}"
                        ),
                        state=_state(
                            outcome.status,
                            results=outcome.results_seen,
                            drift=drift,
                        ),
                        operation_status=outcome.status,
                        discovery_role=outcome.stage.value,
                        requests=outcome.request_attempts,
                        retries=outcome.retry_requests,
                        redirects=outcome.redirect_requests,
                        results=outcome.results_seen,
                        relevant_items=outcome.relevant_items,
                        accepted_observations=outcome.observations_accepted,
                        rejected_items=sum(outcome.rejection_reasons.values()),
                        rejection_reasons={
                            reason.value: count
                            for reason, count in outcome.rejection_reasons.items()
                        },
                        source_family=outcome.source_family,
                        unique_origins=len({item.origin_key for item in observations}),
                        latency_ms=outcome.latency_ms,
                        indexed_social_hosts=social_hosts,
                        error=outcome.error,
                        drift_reason=drift,
                        retryable=outcome.status in _UNAVAILABLE,
                        never_attempted_reason=(
                            outcome.error
                            if outcome.request_attempts == 0
                            and outcome.status is SourceOperationStatus.BUDGET_EXHAUSTED
                            else None
                        ),
                    )
                )
            if techcrunch_provider is not None:
                diagnostics.append(
                    await _search_probe(
                        "techcrunch:feed",
                        techcrunch_provider,
                        "",
                        role="discovery",
                    )
                )
        finally:
            await service.close()

        probe_budget = budget.model_copy(
            update={
                "max_queries": 3,
                "max_total_requests": 3,
                "max_requests_per_provider": 3,
                "max_retry_requests": 1,
                "max_requests_per_domain": 3,
            }
        )
        probe_tracker = RequestBudgetTracker(probe_budget)
        arxiv = ArxivSearchProvider(max_retries=1, page_delay_seconds=0)
        arxiv.set_request_budget(probe_tracker)
        diagnostics.append(
            await _search_probe(
                "arxiv",
                arxiv,
                f'"{program.name}" startup founder',
                role="enrichment",
            )
        )
        wayback_crawler = StealthCrawler()
        wayback = WaybackSearchProvider(wayback_crawler)
        wayback.set_request_budget(probe_tracker)
        website = program.website_urls[0] if program.website_urls else "https://example.com"
        diagnostics.append(await _search_probe("wayback", wayback, website, role="historical"))
        github_crawler = StealthCrawler()
        diagnostics.append(
            await _github_probe(
                github_crawler,
                probe_tracker,
                f'"{program.name}" "{program.batch}"',
            )
        )
        await arxiv.close()
        await wayback.close()
        await github_crawler.close()

        evaluation = evaluate_discovery(
            run.hypotheses,
            ground_truth=ground_truth,
            observations=run.observations,
            program=program,
        )
        observation_by_id = {item.observation_id: item for item in run.observations}
        quality = LiveDiscoveryQuality(
            unknown_hypotheses=len(run.hypotheses),
            promotion_eligible=sum(item.assessment.promotion_eligible for item in run.hypotheses),
            canonical_promotions=0,
            precision_at_k=evaluation.precision_at_k if ground_truth else None,
            recall=evaluation.recall if ground_truth else None,
            false_positive_rate=evaluation.false_positive_rate if ground_truth else None,
            mean_lead_time_days=evaluation.mean_lead_time_days if ground_truth else None,
            source_contribution=evaluation.source_contribution,
            source_ablation_promotion_loss=evaluation.source_ablation_promotion_loss,
            ground_truth_available=bool(ground_truth),
            ground_truth_note=(
                "Metrics computed against the supplied timestamped ground truth."
                if ground_truth
                else "Precision, recall, false positives, and lead time are unavailable "
                "without timestamped ground truth; no value was inferred."
            ),
            funnel=run.funnel,
            zero_state=run.zero_state,
            candidate_examples=[
                LiveCandidateDiagnostic(
                    hypothesis_id=item.hypothesis_id,
                    candidate_name=item.candidate_name,
                    company_name=item.company_name,
                    state=item.state.value,
                    confidence_pct=item.assessment.confidence_pct,
                    identity_confidence_pct=item.assessment.identity_confidence_pct,
                    batch_evidence_present=item.assessment.batch_evidence_present,
                    promotion_eligible=item.assessment.promotion_eligible,
                    promotion_blockers=item.assessment.promotion_blockers,
                    observation_count=len(item.observation_ids),
                    source_urls=list(
                        dict.fromkeys(
                            observation_by_id[observation_id].source_url
                            for observation_id in item.observation_ids
                            if observation_id in observation_by_id
                        )
                    )[:5],
                    claims=[
                        observation_by_id[observation_id].claim
                        for observation_id in item.observation_ids[:3]
                        if observation_id in observation_by_id
                    ],
                )
                for item in run.hypotheses[:10]
            ],
        )

        social_diagnostics: list[SocialPlatformDiagnostic] = []
        indexed_social_outcomes = [
            outcome
            for outcome in run.source_outcomes
            if outcome.source_family == "indexed_social"
        ]
        for platform in ("linkedin", "x", "instagram", "threads"):
            matching = [
                (outcome, outcome.platform_metrics.get(platform, {}))
                for outcome in run.source_outcomes
                if platform in outcome.platform_metrics
            ]
            providers = sorted({outcome.provider_id for outcome in indexed_social_outcomes})
            counts = Counter()
            for _, metrics in matching:
                counts.update(metrics)
            platform_hosts = {
                "linkedin": {"linkedin.com"},
                "x": {"x.com", "twitter.com"},
                "instagram": {"instagram.com"},
                "threads": {"threads.net"},
            }[platform]
            related_observation_ids = {
                item.observation_id
                for item in run.observations
                if item.source_domain in platform_hosts
            }
            related_hypotheses = [
                item
                for item in run.hypotheses
                if related_observation_ids.intersection(item.observation_ids)
            ]
            failed = [
                outcome.error
                for outcome in indexed_social_outcomes
                if outcome.error
            ]
            successful_index = any(
                outcome.status in {
                    SourceOperationStatus.SUCCESS,
                    SourceOperationStatus.PARTIAL,
                    SourceOperationStatus.EMPTY,
                }
                for outcome in indexed_social_outcomes
            )
            social_diagnostics.append(
                SocialPlatformDiagnostic(
                    platform=platform,
                    acquisition_providers=providers,
                    retrieved=counts["retrieved"],
                    relevant=counts["relevant"],
                    observations=counts["observations"],
                    hypotheses=len(related_hypotheses),
                    duplicates=counts["duplicates"],
                    corroborated=sum(
                        item.assessment.identity_confidence_pct >= 80
                        for item in related_hypotheses
                    ),
                    unavailable=not successful_index,
                    unavailability_reasons=sorted(set(failed))[:3],
                    direct_requests=0,
                )
            )

    blocked = sorted(
        item.source_id
        for item in diagnostics
        if item.state in {SourceQualityState.BLOCKED, SourceQualityState.UNAVAILABLE}
    )
    typed_osint_diagnostics = [
        DedicatedOsintToolDiagnostic(**item) for item in osint_diagnostics
    ]
    report_data = {
        "run_id": str(uuid.uuid4()),
        "program_id": program.id,
        "batch_id": program.batch,
        "live_network_executed": True,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc),
        "budget": budget,
        "control_budget_usage": control_tracker.snapshot(),
        "discovery_budget_usage": run.request_accounting,
        "stop_reason": run.stop_reason,
        "source_diagnostics": diagnostics,
        "connector_inventory": build_connector_inventory(
            diagnostics,
            typed_osint_diagnostics,
        ),
        "social_platform_diagnostics": social_diagnostics,
        "dedicated_osint_tools": typed_osint_diagnostics,
        "osint_public_profile_origins": len(osint_clues),
        "osint_cross_tool_duplicates": sum(
            item["duplicates"] for item in osint_diagnostics
        ),
        "discovery_quality": quality,
        "blocked_or_unavailable_sources": blocked,
        "fixture_only_capabilities": [
            "Google Scholar direct public HTML",
        ],
        "operational_blind_spots": [
            "Search indexes may omit or delay restricted-social announcements.",
            "Recall and lead time require an independent timestamped ground-truth dataset.",
            "Public endpoints can rate-limit, block, or change schema between runs.",
        ],
    }
    report = OperationalValidationReport(
        **report_data,
        report_digest=stable_digest(report_data),
    )
    SourceQualityReportStore(report_path).save(report)
    return report
