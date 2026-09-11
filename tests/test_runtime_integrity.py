"""Milestone 1A regression tests for truthful bounded runtime behavior."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from venture_intelligence.api.app import app
from venture_intelligence.api.routers import v1
from venture_intelligence.config import Settings, settings
from venture_intelligence.connectors.directory import DirectoryConnector
from venture_intelligence.connectors.environmental import SocialConnector
from venture_intelligence.connectors.factory import build_connectors
from venture_intelligence.connectors.public_sources import LinkedInConnector
from venture_intelligence.crawler import CrawlResult, StealthCrawler
from venture_intelligence.knowledge.compiler import KnowledgeCompiler
from venture_intelligence.observability.metrics import metrics
from venture_intelligence.pipeline.graph import GraphBuilder
from venture_intelligence.programs import ProgramConfig, ProgramRegistry
from venture_intelligence.research.agent import ResearchAgent
from venture_intelligence.research.query import (
    normalize_query_text,
    quote_query_term,
    scoped_query,
)
from venture_intelligence.research.request_budget import RequestBudgetTracker
from venture_intelligence.research.sources import (
    DuckDuckGoSearchProvider,
    PublicProgramAnnouncementProvider,
    RssSearchProvider,
    SearchResponse,
    SearchResult,
)
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EntityType,
    EventType,
    ResearchBudget,
    ResearchSourceStatus,
    ResearchTarget,
    ResearchTargetType,
    Source,
)
from venture_intelligence.storage.db import Database


def _registry(*, strategy: str = "triangulation") -> ProgramRegistry:
    return ProgramRegistry(
        [
            ProgramConfig(
                id="test_program",
                name="Test Program",
                batch="T01",
                detection_strategy=strategy,
                directory_url=(
                    "https://directory.test/companies" if strategy == "directory" else None
                ),
                base_confidence=0.9,
                keywords=["Test Program"],
                entity_id_prefix="test",
            )
        ]
    )


def _event(
    *,
    source: Source = Source.HACKER_NEWS,
    entity_type: EntityType = EntityType.COMPANY,
) -> CanonicalEvent:
    return CanonicalEvent(
        event_type=(
            EventType.REPO_CREATED
            if entity_type == EntityType.REPOSITORY
            else EventType.HN_MENTIONED
        ),
        entity_type=entity_type,
        entity_id="candidate__acme",
        source=source,
        source_url="https://example.test/acme",
        occurred_at=datetime.now(timezone.utc),
        confidence=0.5,
        raw_payload={"name": "Acme Labs"},
    )


def test_query_normalization_quotes_exactly_once() -> None:
    assert normalize_query_text("  Acme   Labs  ") == "Acme Labs"
    assert normalize_query_text('"Acme Labs"') == "Acme Labs"
    assert quote_query_term('"Acme Labs"') == '"Acme Labs"'
    special = scoped_query(
        "site:linkedin.com",
        "Y Combinator",
        '"O\'Reilly + Labs"',
        suffix="announcement",
    )
    assert special == (
        'site:linkedin.com "Y Combinator" "O\'Reilly + Labs" announcement'
    )
    assert special.count('"') == 4
    structured = scoped_query(
        "site:x.com",
        "Y Combinator",
        suffix="announcement",
        structured_query='site:linkedin.com/in "Jane Smith" building',
    )
    assert structured == (
        'site:x.com "Y Combinator" '
        'site:linkedin.com/in "Jane Smith" building announcement'
    )
    assert '"site:linkedin.com' not in structured


async def test_actual_request_budget_reserves_each_redirect_hop() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "/next"},
        )

    crawler = StealthCrawler(Settings(_env_file=None))
    crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    provider = DuckDuckGoSearchProvider(crawler, max_retries=0)
    provider.search_endpoint = "https://redirect-budget.test/start?q={query}"
    budget = ResearchBudget(
        max_queries=1,
        max_depth=0,
        max_results_per_query=1,
        max_runtime_seconds=5,
        max_total_requests=1,
        max_requests_per_provider=1,
        max_retry_requests=0,
        max_requests_per_domain=1,
    )
    try:
        run = await ResearchAgent(provider).run(
            ResearchTarget(name="Acme Labs", type=ResearchTargetType.COMPANY),
            budget,
        )
    finally:
        await crawler.close()
    assert calls == ['https://redirect-budget.test/start?q=%22Acme+Labs%22']
    assert crawler.request_count == 1
    assert run.requests_used == 1
    assert run.redirect_requests_used == 0
    assert run.stop_reason == "request_budget_exhausted"

    redirected_calls: list[str] = []

    def successful_redirect(request: httpx.Request) -> httpx.Response:
        redirected_calls.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(
                302,
                request=request,
                headers={"Location": "/final"},
            )
        return httpx.Response(200, request=request, text="<html>no results</html>")

    redirected_crawler = StealthCrawler(Settings(_env_file=None))
    redirected_crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(successful_redirect),
        follow_redirects=False,
    )
    redirected_provider = DuckDuckGoSearchProvider(redirected_crawler, max_retries=0)
    redirected_provider.search_endpoint = "https://redirect-budget.test/start?q={query}"
    redirected_tracker = RequestBudgetTracker(
        budget.model_copy(
            update={
                "max_total_requests": 2,
                "max_requests_per_provider": 2,
                "max_requests_per_domain": 2,
            }
        )
    )
    redirected_provider.set_request_budget(redirected_tracker)
    try:
        response = await redirected_provider.search("Acme Labs", 1)
    finally:
        await redirected_crawler.close()
    assert len(redirected_calls) == 2
    assert response.attempts == 1
    assert response.retry_requests == 0
    assert response.redirect_requests == 1
    assert redirected_tracker.requests_used == 2
    assert redirected_tracker.redirect_requests_used == 1


async def test_search_redirect_rechecks_restricted_and_private_target_policy() -> None:
    cases = [
        ("https://x.com/acme", ResearchSourceStatus.BLOCKED),
        ("http://127.0.0.1/private", ResearchSourceStatus.FAILED),
    ]
    for destination, expected_status in cases:
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(
                302,
                request=request,
                headers={"Location": destination},
            )

        crawler = StealthCrawler(Settings(_env_file=None))
        crawler._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
        )
        provider = DuckDuckGoSearchProvider(crawler, max_retries=0)
        provider.search_endpoint = "https://search-policy.test/start?q={query}"
        tracker = RequestBudgetTracker(
            ResearchBudget(
                max_total_requests=4,
                max_requests_per_provider=4,
                max_retry_requests=0,
                max_requests_per_domain=4,
            )
        )
        provider.set_request_budget(tracker)
        try:
            response = await provider.search("Acme Labs", 1)
        finally:
            await crawler.close()
        assert response.status == expected_status
        assert calls == ["https://search-policy.test/start?q=Acme+Labs"]
        assert crawler.request_count == 1
        assert tracker.requests_used == 1


async def test_sensitive_request_headers_never_cross_redirect_origins() -> None:
    observed: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append((request.url.host or "", request.headers.get("authorization")))
        if request.url.host == "api.github.com":
            return httpx.Response(
                302,
                request=request,
                headers={"Location": "https://downloads.example.test/final"},
            )
        return httpx.Response(200, request=request, text="{}")

    crawler = StealthCrawler(Settings(_env_file=None))
    crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    tracker = RequestBudgetTracker(
        ResearchBudget(
            max_queries=1,
            max_depth=0,
            max_results_per_query=1,
            max_runtime_seconds=5,
            max_total_requests=2,
            max_requests_per_provider=2,
            max_retry_requests=0,
            max_requests_per_domain=2,
        )
    )
    try:
        result = await crawler.fetch_text_budgeted(
            "https://api.github.com/start",
            tracker,
            "github-control",
            headers={"Authorization": "Bearer should-not-leak"},
        )
    finally:
        await crawler.close()

    assert result.ok
    assert observed == [
        ("api.github.com", "Bearer should-not-leak"),
        ("downloads.example.test", None),
    ]


async def test_authorized_post_redirect_preserves_method_but_not_cross_origin_secret() -> None:
    observed: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(
            (request.url.host or "", request.method, request.headers.get("authorization"))
        )
        if request.url.host == "api.producthunt.com":
            return httpx.Response(
                307,
                request=request,
                headers={"Location": "https://public-api.example.test/graphql"},
            )
        return httpx.Response(200, request=request, json={"data": {}})

    crawler = StealthCrawler(Settings(_env_file=None))
    crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    try:
        result = await crawler.post_json_text(
            "https://api.producthunt.com/v2/api/graphql",
            {"query": "query { viewer { id } }"},
            headers={"Authorization": "Bearer should-not-leak"},
        )
    finally:
        await crawler.close()

    assert result.ok
    assert observed == [
        ("api.producthunt.com", "POST", "Bearer should-not-leak"),
        ("public-api.example.test", "POST", None),
    ]


async def test_equivalent_host_aliases_share_budget_and_circuit_identity() -> None:
    tracker = RequestBudgetTracker(
        ResearchBudget(
            max_total_requests=5,
            max_requests_per_provider=5,
            max_retry_requests=0,
            max_requests_per_domain=2,
        )
    )
    assert await tracker.acquire("provider", "Example.COM")
    assert await tracker.acquire("provider", "example.com.:443")
    assert not await tracker.acquire("provider", "EXAMPLE.COM:443")
    assert tracker.domain_requests == {"example.com": 2}

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(503, request=request, text="unavailable")

    crawler = StealthCrawler(
        Settings(
            _env_file=None,
            crawler_failure_threshold=2,
            crawler_circuit_cooldown_seconds=60,
        )
    )
    crawler._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await crawler.fetch_text("https://Example.COM/one")
        await crawler.fetch_text("https://example.com.:443/two")
        blocked = await crawler.fetch_text("https://EXAMPLE.COM:443/three")
    finally:
        await crawler.close()
    assert len(calls) == 2
    assert "circuit open" in (blocked.error or "")
    assert set(crawler._circuits) == {"example.com"}


async def test_budget_exhaustion_is_not_cached_across_research_runs() -> None:
    class AcceptedCrawler:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_text(self, url: str) -> CrawlResult:
            self.calls += 1
            return CrawlResult(url, "accepted", 202)

        async def close(self) -> None:
            return None

    crawler = AcceptedCrawler()
    agent = ResearchAgent(
        DuckDuckGoSearchProvider(crawler, max_retries=1, base_backoff_seconds=0)
    )
    target = ResearchTarget(name="Acme Labs", type=ResearchTargetType.COMPANY)
    first = ResearchBudget(
        max_queries=1,
        max_depth=0,
        max_results_per_query=1,
        max_runtime_seconds=5,
        max_total_requests=1,
        max_requests_per_provider=1,
        max_retry_requests=0,
        max_requests_per_domain=1,
    )
    second = first.model_copy(
        update={
            "max_total_requests": 2,
            "max_requests_per_provider": 2,
            "max_retry_requests": 1,
            "max_requests_per_domain": 2,
        }
    )
    first_run = await agent.run(target, first)
    second_run = await agent.run(target, second)
    assert first_run.stop_reason == "request_budget_exhausted"
    assert second_run.requests_used == 2
    assert crawler.calls == 3


async def test_actual_request_budget_bounds_retries_and_reports_stop_reason() -> None:
    class AcceptedCrawler:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_text(self, url: str) -> CrawlResult:
            self.calls += 1
            return CrawlResult(url, "accepted", 202, headers={"Retry-After": "0"})

        async def close(self) -> None:
            return None

    crawler = AcceptedCrawler()
    provider = DuckDuckGoSearchProvider(
        crawler,
        max_retries=5,
        base_backoff_seconds=0,
    )
    budget = ResearchBudget(
        max_queries=1,
        max_depth=0,
        max_results_per_query=1,
        max_runtime_seconds=5,
        max_total_requests=2,
        max_requests_per_provider=2,
        max_retry_requests=1,
        max_requests_per_domain=2,
    )
    run = await ResearchAgent(provider).run(
        ResearchTarget(name="Acme Labs", type=ResearchTargetType.COMPANY),
        budget,
    )
    assert crawler.calls == 2
    assert run.requests_used == 2
    assert run.retry_requests_used == 1
    assert run.request_budget_remaining == 0
    assert run.stop_reason == "request_budget_exhausted"


async def test_actual_request_budget_bounds_scoped_query_amplification() -> None:
    class EmptyCrawler:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_text(self, url: str) -> CrawlResult:
            self.calls += 1
            return CrawlResult(url, "<html>no results</html>", 200)

        async def close(self) -> None:
            return None

    crawler = EmptyCrawler()
    provider = PublicProgramAnnouncementProvider(crawler, max_scoped_queries=6)
    budget = ResearchBudget(
        max_queries=1,
        max_depth=0,
        max_results_per_query=1,
        max_runtime_seconds=5,
        max_total_requests=2,
        max_requests_per_provider=2,
        max_retry_requests=0,
        max_requests_per_domain=2,
    )
    run = await ResearchAgent(provider).run(
        ResearchTarget(name="Acme Labs", type=ResearchTargetType.COMPANY),
        budget,
    )
    assert crawler.calls == 2
    assert run.requests_used == 2
    assert run.provider_request_counts == {"public-program-osint": 2}
    assert run.retry_requests_used == 0
    assert run.sources[0].retries_used == 0
    assert run.stop_reason == "request_budget_exhausted"


async def test_crawler_normalizes_transport_source_and_redirect_semantics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        cases = {
            "/accepted": (202, b"accepted"),
            "/blocked": (403, b"forbidden"),
            "/rate": (429, b"rate limited"),
            "/usable": (200, b"usable public content"),
            "/start": (307, b""),
            "/final": (200, b"final public content"),
        }
        status_code, body = cases[request.url.path]
        headers = {"Location": "/final"} if request.url.path == "/start" else {}
        return httpx.Response(
            status_code,
            request=request,
            content=body,
            headers=headers,
        )

    crawler = StealthCrawler(
        Settings(_env_file=None, crawler_failure_threshold=10)
    )
    crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    try:
        accepted = await crawler.fetch_text("https://runtime-status.test/accepted")
        blocked = await crawler.fetch_text("https://runtime-status.test/blocked")
        rate = await crawler.fetch_text("https://runtime-status.test/rate")
        usable = await crawler.fetch_text("https://runtime-status.test/usable")
        redirected = await crawler.fetch_text("https://runtime-status.test/start")
    finally:
        await crawler.close()
    assert accepted.source_status == ResearchSourceStatus.TEMPORARILY_UNAVAILABLE
    assert not accepted.ok
    assert blocked.source_status == ResearchSourceStatus.BLOCKED
    assert rate.source_status == ResearchSourceStatus.RATE_LIMITED
    assert usable.ok and usable.source_status == ResearchSourceStatus.AVAILABLE
    assert redirected.url == "https://runtime-status.test/final"
    assert redirected.original_url == "https://runtime-status.test/start"
    health = metrics.snapshot()["source_health"]["runtime-status.test"]
    assert health["transport_successes"] == 6
    assert health["usable_successes"] == 2
    assert health["source_status_counts"]["TEMPORARILY_UNAVAILABLE"] == 1
    assert health["source_status_counts"]["REDIRECT"] == 1


async def test_login_content_and_timeout_are_unusable_and_open_circuit() -> None:
    login_calls = 0

    def login_handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_calls
        login_calls += 1
        return httpx.Response(200, request=request, content=b"Sign in to continue")

    runtime = Settings(
        _env_file=None,
        crawler_failure_threshold=2,
        crawler_circuit_cooldown_seconds=60,
    )
    login_crawler = StealthCrawler(runtime)
    login_crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(login_handler)
    )
    try:
        first = await login_crawler.fetch_text("https://www.instagram.com/a")
        await login_crawler.fetch_text("https://www.instagram.com/b")
        third = await login_crawler.fetch_text("https://www.instagram.com/c")
    finally:
        await login_crawler.close()
    assert first.source_status == ResearchSourceStatus.AUTH_REQUIRED
    assert "circuit open" in (third.error or "")
    assert login_calls == 2

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    timeout_crawler = StealthCrawler(runtime)
    timeout_crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_handler)
    )
    try:
        timed_out = await timeout_crawler.fetch_text("https://timeout.test/a")
    finally:
        await timeout_crawler.close()
    assert timed_out.source_status == ResearchSourceStatus.UNAVAILABLE
    assert not timed_out.ok


async def test_rendered_crawl_obeys_budget_auth_classification_and_circuit(
    monkeypatch,
) -> None:
    process_calls = 0

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return (
                b'{"ok":true,"status_code":200,"final_url":'
                b'"https://www.instagram.com/login","text":"Sign in",'
                b'"error":null}',
                b"",
            )

    async def fake_exec(*_args, **_kwargs):
        nonlocal process_calls
        process_calls += 1
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    runtime = Settings(
        _env_file=None,
        crawler_render_enabled=True,
        crawler_engine="crawlee",
        crawler_failure_threshold=10,
        connector_timeout_seconds=1,
        restricted_social_access_mode="approved_public_web",
    )
    crawler = StealthCrawler(runtime)
    budget = RequestBudgetTracker(
        ResearchBudget(
            max_total_requests=1,
            max_requests_per_provider=1,
            max_retry_requests=0,
            max_requests_per_domain=1,
        )
    )
    first = await crawler.fetch_rendered_text(
        "https://www.instagram.com/acme",
        request_budget=budget,
    )
    second = await crawler.fetch_rendered_text(
        "https://www.instagram.com/acme",
        request_budget=budget,
    )
    assert first.source_status == ResearchSourceStatus.AUTH_REQUIRED
    assert second.source_status == ResearchSourceStatus.BUDGET_EXHAUSTED
    assert crawler.request_count == 1
    assert process_calls == 1

    circuit_crawler = StealthCrawler(
        runtime.model_copy(update={"crawler_failure_threshold": 2})
    )
    await circuit_crawler.fetch_rendered_text("https://www.instagram.com/one")
    await circuit_crawler.fetch_rendered_text("https://www.instagram.com/two")
    circuit_result = await circuit_crawler.fetch_rendered_text(
        "https://www.instagram.com/three"
    )
    assert "circuit open" in (circuit_result.error or "")
    assert circuit_crawler.request_count == 2
    assert process_calls == 3


async def test_rendered_redirects_are_blocked_and_subresources_are_disabled(
    monkeypatch,
) -> None:
    final_urls = iter(
        [
            "https://x.com/acme",
            "http://127.0.0.1/private",
        ]
    )

    async def fake_render(_url: str) -> CrawlResult:
        return CrawlResult(next(final_urls), "redirected", 200)

    crawler = StealthCrawler(
        Settings(
            _env_file=None,
            crawler_render_enabled=True,
            crawler_engine="crawlee",
        )
    )
    monkeypatch.setattr(crawler, "_fetch_with_crawlee", fake_render)
    direct_restricted = await crawler.fetch_rendered_text(
        "https://x.com/direct"
    )
    restricted = await crawler.fetch_rendered_text(
        "https://render-policy.test/restricted"
    )
    private = await crawler.fetch_rendered_text(
        "https://render-policy.test/private"
    )
    assert direct_restricted.source_status == ResearchSourceStatus.BLOCKED
    assert not direct_restricted.request_made
    assert restricted.source_status == ResearchSourceStatus.BLOCKED
    assert "cross-host" in (restricted.error or "")
    assert private.source_status == ResearchSourceStatus.FAILED
    assert "unsafe redirect destination" in (private.error or "")
    assert crawler.request_count == 2

    script = Path("scripts/crawlee_public_fetcher.mjs").read_text(
        encoding="utf-8"
    )
    assert "request.redirectedFrom() === null" in script
    assert "await route.abort()" in script
    assert "maxRequestRetries: 0" in script


async def test_crawlee_timeout_terminates_and_reaps_child(monkeypatch) -> None:
    class TimedOutProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.terminated = False
            self.killed = False
            self.reaped = False

        async def communicate(self):
            await asyncio.Future()

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.reaped = True
            self.returncode = -15
            return self.returncode

    process = TimedOutProcess()

    async def fake_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    crawler = StealthCrawler(
        Settings(_env_file=None, connector_timeout_seconds=0.01)
    )
    result = await crawler._fetch_with_crawlee(
        "https://render-timeout.test/page"
    )
    assert "timed out" in (result.error or "")
    assert process.terminated
    assert process.reaped
    assert not process.killed


async def test_evidence_metrics_are_recorded_only_after_usable_content() -> None:
    responses = iter(
        [
            httpx.Response(200, text="matching content"),
            httpx.Response(200, text="usable but empty"),
            httpx.Response(202, text="accepted"),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        response.request = request
        return response

    crawler = StealthCrawler(Settings(_env_file=None, crawler_failure_threshold=10))
    crawler._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        first = await crawler.fetch_text("https://evidence-metrics-review.test/one")
        crawler.record_evidence_outcome([first.url])
        await crawler.fetch_text("https://evidence-metrics-review.test/two")
        crawler.record_evidence_outcome([])
        await crawler.fetch_text("https://evidence-metrics-review.test/three")
        crawler.record_evidence_outcome([])
    finally:
        await crawler.close()
    health = metrics.snapshot()["source_health"]["evidence-metrics-review.test"]
    assert health["transport_successes"] == 3
    assert health["usable_successes"] == 2
    assert health["evidence_successes"] == 1
    assert health["evidence_empty"] == 1

    class EvidenceProvider:
        domain = "research-evidence-review.test"

        async def search(self, query: str, max_results: int) -> SearchResponse:
            return SearchResponse(
                query=query,
                status=ResearchSourceStatus.AVAILABLE,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                results=[
                    SearchResult(
                        url="https://acme.test/about",
                        title="Acme Labs",
                        source_domain="acme.test",
                    )
                ][:max_results],
            )

    await ResearchAgent(EvidenceProvider()).run(
        ResearchTarget(name="Acme Labs", type=ResearchTargetType.COMPANY),
        ResearchBudget(max_queries=1, max_depth=0, max_runtime_seconds=5),
    )
    research_health = metrics.snapshot()["source_health"][
        "research-evidence-review.test"
    ]
    assert research_health["evidence_successes"] == 1
    assert research_health["evidence_empty"] == 0


async def test_evidence_metrics_survive_request_buffer_rollover() -> None:
    domain = "metrics-rollover-m1a.test"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, text="usable")

    before = metrics.snapshot()["source_health"].get(domain, {})
    before_successes = int(before.get("evidence_successes", 0))
    crawler = StealthCrawler(Settings(_env_file=None))
    crawler._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        for index in range(105):
            operation_id, token = crawler.begin_evidence_operation()
            try:
                result = await crawler.fetch_text(
                    f"https://{domain}/item-{index}"
                )
            finally:
                crawler.end_evidence_operation(token)
            crawler.record_evidence_outcome(
                [result.url],
                operation_id=operation_id,
            )
    finally:
        await crawler.close()
    health = metrics.snapshot()["source_health"][domain]
    assert len(crawler.request_outcomes) == 100
    assert health["evidence_successes"] - before_successes == 105
    assert crawler._operation_outcomes == {}


async def test_concurrent_evidence_metrics_remain_operation_attributed() -> None:
    success_domain = "metrics-concurrent-success-m1a.test"
    empty_domain = "metrics-concurrent-empty-m1a.test"
    arrived = 0
    both_arrived = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_arrived.set()
        await both_arrived.wait()
        return httpx.Response(200, request=request, text="usable")

    crawler = StealthCrawler(Settings(_env_file=None))
    crawler._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run_operation(domain: str, produces_evidence: bool) -> None:
        operation_id, token = crawler.begin_evidence_operation()
        try:
            result = await crawler.fetch_text(f"https://{domain}/page")
        finally:
            crawler.end_evidence_operation(token)
        crawler.record_evidence_outcome(
            [result.url] if produces_evidence else [],
            operation_id=operation_id,
        )

    before = metrics.snapshot()["source_health"]
    before_success = int(
        before.get(success_domain, {}).get("evidence_successes", 0)
    )
    before_success_empty = int(
        before.get(success_domain, {}).get("evidence_empty", 0)
    )
    before_empty = int(
        before.get(empty_domain, {}).get("evidence_empty", 0)
    )
    before_empty_success = int(
        before.get(empty_domain, {}).get("evidence_successes", 0)
    )
    try:
        await asyncio.gather(
            run_operation(success_domain, True),
            run_operation(empty_domain, False),
        )
    finally:
        await crawler.close()
    health = metrics.snapshot()["source_health"]
    assert (
        health[success_domain]["evidence_successes"] - before_success
        == 1
    )
    assert health[success_domain]["evidence_empty"] == before_success_empty
    assert health[empty_domain]["evidence_empty"] - before_empty == 1
    assert health[empty_domain]["evidence_successes"] == before_empty_success


async def test_partial_budget_exhaustion_records_preserved_evidence() -> None:
    domain = "partial-budget-evidence-m1a.test"

    class PartialProvider:
        domain = "partial-budget-evidence-m1a.test"

        async def search(self, query: str, max_results: int) -> SearchResponse:
            return SearchResponse(
                query=query,
                status=ResearchSourceStatus.BUDGET_EXHAUSTED,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                error="max_total_requests_reached",
                results=[
                    SearchResult(
                        url="https://acme.test/about",
                        title="Acme Labs official company page",
                        snippet="Acme Labs company information",
                        source_domain="acme.test",
                    )
                ][:max_results],
            )

    before = metrics.snapshot()["source_health"].get(domain, {})
    before_successes = int(before.get("evidence_successes", 0))
    before_empty = int(before.get("evidence_empty", 0))
    run = await ResearchAgent(PartialProvider()).run(
        ResearchTarget(name="Acme Labs", type=ResearchTargetType.COMPANY),
        ResearchBudget(max_queries=1, max_depth=0, max_runtime_seconds=5),
    )
    health = metrics.snapshot()["source_health"][domain]
    assert run.stop_reason == "request_budget_exhausted"
    assert len(run.evidence) == 1
    assert run.evidence[0].canonical_url == "https://acme.test/about"
    assert run.sources[0].status == ResearchSourceStatus.BUDGET_EXHAUSTED
    assert run.queries_succeeded == 1
    assert health["evidence_successes"] - before_successes == 1
    assert health["evidence_empty"] == before_empty


async def test_rss_preserves_partial_evidence_when_budget_is_exhausted() -> None:
    class FeedCrawler:
        async def fetch_text(self, url: str) -> CrawlResult:
            return CrawlResult(
                url,
                "<rss><channel><item><title>Acme</title>"
                "<link>https://acme.test/post</link>"
                "<description>Acme launch</description>"
                "</item></channel></rss>",
                200,
            )

        async def close(self) -> None:
            return None

    provider = RssSearchProvider(
        ["https://feed-one.test/rss", "https://feed-two.test/rss"],
        FeedCrawler(),
    )
    provider.set_request_budget(
        RequestBudgetTracker(
            ResearchBudget(
                max_total_requests=1,
                max_requests_per_provider=1,
                max_retry_requests=0,
                max_requests_per_domain=1,
            )
        )
    )
    response = await provider.search("acme", 5)
    assert response.status == ResearchSourceStatus.BUDGET_EXHAUSTED
    assert response.attempts == 1
    assert [item.url for item in response.results] == ["https://acme.test/post"]


async def test_default_monitor_connectors_never_contact_restricted_social_sites() -> None:
    class TrackingCrawler:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def fetch_text(self, url: str) -> CrawlResult:
            self.urls.append(url)
            return CrawlResult(url, "unexpected", 200)

        async def fetch_rendered_text(self, url: str) -> CrawlResult:
            self.urls.append(url)
            return CrawlResult(url, "unexpected", 200)

        async def close(self) -> None:
            return None

    crawler = TrackingCrawler()
    runtime = Settings(_env_file=None)
    connectors = build_connectors(
        _registry(),
        crawler,
        runtime_settings=runtime,
    )
    social = next(item for item in connectors if isinstance(item, SocialConnector))
    linkedin = next(item for item in connectors if isinstance(item, LinkedInConnector))
    assert await social.scan() == []
    assert not linkedin.can_investigate(_event(entity_type=EntityType.PERSON))
    assert crawler.urls == []


async def test_indexed_social_search_is_allowed_but_direct_hosts_remain_disabled() -> None:
    class TrackingCrawler:
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def fetch_text(self, url: str) -> CrawlResult:
            self.urls.append(url)
            return CrawlResult(url, "Acme Labs Test Program", 200)

        async def close(self) -> None:
            return None

    search_url = (
        "https://html.duckduckgo.com/html/"
        "?q=site%3Ax.com+%22{query}%22+building"
    )
    registry = ProgramRegistry(
        [
            ProgramConfig(
                id="test_program",
                name="Test Program",
                batch="T01",
                detection_strategy="triangulation",
                base_confidence=0.8,
                keywords=["Test Program"],
                entity_id_prefix="test",
                social_search_urls=[search_url, "https://x.com/search?q={query}"],
            )
        ]
    )
    crawler = TrackingCrawler()
    connector = SocialConnector(
        registry,
        crawler,
        runtime_settings=Settings(_env_file=None),
    )
    events = await connector.investigate(
        CanonicalEvent(
            event_type=EventType.COMPANY_LISTED,
            entity_type=EntityType.COMPANY,
            entity_id="test__acme-labs",
            source=Source.API,
            source_url="api://test",
            occurred_at=datetime.now(timezone.utc),
            confidence=0.5,
            raw_payload={"name": "Acme Labs"},
            metadata={"program_id": "test_program"},
        )
    )
    assert len(events) == 1
    assert crawler.urls == [
        "https://html.duckduckgo.com/html/"
        "?q=site%3Ax.com+%22Acme+Labs%22+building"
    ]
    assert "x.com" in events[0].source_url
    assert events[0].source_url.startswith("https://html.duckduckgo.com/")


async def test_directory_reports_success_empty_drift_parser_failure_and_final_url() -> None:
    assert DirectoryConnector._document_status(
        '<div data-program-directory="companies"></div>', []
    ) == "SUCCESS_EMPTY"
    assert DirectoryConnector._document_status(
        '<div data-page="Companies/IndexPage currentBatch"></div>', []
    ) == "SOURCE_DRIFT"
    assert DirectoryConnector._document_status("<html>unexpected</html>", []) == "PARSER_FAILURE"

    class DirectoryCrawler:
        async def fetch_text(self, url: str) -> CrawlResult:
            return CrawlResult(
                "https://directory.test/final",
                '<a href="/companies/acme">Acme Labs</a>',
                200,
                source_status=ResearchSourceStatus.AVAILABLE,
                original_url=url,
            )

        async def close(self) -> None:
            return None

    events = await DirectoryConnector(_registry(strategy="directory"), DirectoryCrawler()).scan()
    assert len(events) == 1
    assert events[0].source_url == "https://directory.test/final"
    connector_health = metrics.snapshot()["connector_health"]["directory"]
    assert connector_health["status"] == "SUCCESS"
    assert connector_health["final_url"] == "https://directory.test/final"


async def test_zero_evidence_run_creates_investigation_not_canonical_entity(tmp_path) -> None:
    class EmptyProvider:
        domain = "empty.test"

        async def search(self, query: str, max_results: int) -> SearchResponse:
            return SearchResponse(
                query=query,
                status=ResearchSourceStatus.UNAVAILABLE,
                results=[],
                retrieved_at="2026-08-21T00:00:00+00:00",
                error="no public results",
            )

        async def close(self) -> None:
            return None

    compiler = KnowledgeCompiler(tmp_path / "knowledge")
    budget = ResearchBudget(max_queries=1, max_depth=0, max_runtime_seconds=5)
    run = await ResearchAgent(EmptyProvider(), knowledge_compiler=compiler).run(
        ResearchTarget(name="Empty Labs", type=ResearchTargetType.COMPANY),
        budget,
    )
    assert run.evidence == [] and run.entities == [] and run.confidence == 0
    assert compiler.load("company", "Empty Labs") is None
    candidate = compiler.load("investigation", "candidate-empty-labs")
    assert candidate is not None
    assert candidate.identity["validation_state"] == "unverified"
    compiler.compile_page("company", "Plural Labs", evidence=[])
    assert (tmp_path / "knowledge" / "companies" / "plural-labs.json").exists()
    assert not (tmp_path / "knowledge" / "companys").exists()


def test_empty_graph_has_no_fabricated_reference_nodes(tmp_path) -> None:
    database = Database(str(tmp_path / "empty-graph.db"))
    GraphBuilder(database, _registry())
    with database._connect() as connection:
        node_count = connection.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        edge_count = connection.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
    assert node_count == 0
    assert edge_count == 0


def test_graph_creates_registry_reference_only_when_evidence_event_arrives(tmp_path) -> None:
    database = Database(str(tmp_path / "event-graph.db"))
    builder = GraphBuilder(database, _registry(strategy="directory"))
    event = CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id="test__acme-labs",
        source=Source.DIRECTORY,
        source_url="https://directory.test/companies/acme-labs",
        occurred_at=datetime.now(timezone.utc),
        confidence=0.9,
        raw_payload={"name": "Acme Labs", "program_id": "test_program"},
        metadata={"program_id": "test_program"},
    )
    builder.process(event, "company__acme-labs")
    with database._connect() as connection:
        nodes = connection.execute(
            "SELECT node_id, label, properties FROM graph_nodes ORDER BY node_id"
        ).fetchall()
        edges = connection.execute("SELECT * FROM graph_edges").fetchall()
    assert {row["node_id"] for row in nodes} == {
        "accelerator__test",
        "company__acme-labs",
    }
    assert len(edges) == 1
    assert edges[0]["target_id"] == "accelerator__test"


def test_gather_api_reports_attempts_failures_and_candidate_rejection(
    tmp_path,
    monkeypatch,
) -> None:
    class Connector:
        def __init__(self, source_name, result, request_count, crawl_outcomes) -> None:
            self.source_name = source_name
            self.result = result
            self.crawler = SimpleNamespace(
                request_count=request_count,
                request_outcomes=crawl_outcomes,
            )

        def can_investigate(self, _event) -> bool:
            return True

        async def investigate(self, _event):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

        async def close(self) -> None:
            return None

    connectors = [
        Connector("noop", [], 0, []),
        Connector(
            "hacker_news",
            [_event()],
            1,
            [
                {
                    "url": "https://example.test/acme",
                    "status_code": 200,
                    "source_status": "AVAILABLE",
                    "usable": True,
                    "error": None,
                }
            ],
        ),
        Connector(
            "failed",
            RuntimeError("offline"),
            1,
            [
                {
                    "url": "https://failed.test",
                    "status_code": 503,
                    "source_status": "TEMPORARILY_UNAVAILABLE",
                    "usable": False,
                    "error": "offline",
                }
            ],
        ),
    ]
    monkeypatch.setattr(v1, "build_connectors", lambda *_args, **_kwargs: connectors)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/intel/gather",
            json={"entity_name": "Acme Labs"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "preview_corroboration"
    assert payload["persisted_events"] == 0
    assert payload["sources_attempted"] == ["hacker_news", "failed"]
    assert payload["events_found"] == 1
    assert payload["events_accepted"] == 0
    assert payload["events_rejected"] == 1
    outcomes = {item["source"]: item for item in payload["outcomes"]}
    assert outcomes["noop"]["status"] == "SKIPPED"
    assert outcomes["hacker_news"]["status"] == "CANDIDATE_REJECTED"
    assert outcomes["failed"]["status"] == "FAILED"


def test_gather_openapi_exposes_a_closed_status_enum() -> None:
    schema = app.openapi()
    outcome = schema["components"]["schemas"]["GatherSourceOutcome"]
    status_ref = outcome["properties"]["status"]["$ref"]
    status_name = status_ref.rsplit("/", 1)[-1]
    values = set(schema["components"]["schemas"][status_name]["enum"])
    assert {
        "SUCCESS",
        "SUCCESS_EMPTY",
        "SKIPPED",
        "CANDIDATE_REJECTED",
        "TIMEOUT",
        "FAILED",
        "AUTH_REQUIRED",
        "BLOCKED",
        "RATE_LIMITED",
        "TEMPORARILY_UNAVAILABLE",
        "UNAVAILABLE",
        "BUDGET_EXHAUSTED",
    } <= values
