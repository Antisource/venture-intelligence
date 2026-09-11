"""Deterministic coverage for bounded free/public research orchestration."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from venture_intelligence.config import Settings
from venture_intelligence.crawler import CrawlResult
from venture_intelligence.research.agent import ResearchAgent
from venture_intelligence.research.query import QueryGenerator, is_probable_person_name
from venture_intelligence.research.sources import (
    ArxivSearchProvider,
    DuckDuckGoSearchProvider,
    GoogleScholarSearchProvider,
    PublicProgramAnnouncementProvider,
    SearchResponse,
    SearchResult,
    WaybackSearchProvider,
    _status_for_result,
)
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    ResearchBudget,
    ResearchSourceStatus,
    ResearchTarget,
    ResearchTargetType,
)
from venture_intelligence.storage.db import Database


def _response(query: str, *results: SearchResult) -> SearchResponse:
    return SearchResponse(
        query=query,
        status=ResearchSourceStatus.AVAILABLE,
        http_status=200,
        results=list(results),
        retrieved_at="2026-01-01T00:00:00+00:00",
    )


class FakeProvider:
    domain = "duckduckgo.com"

    def __init__(self, responses: list[SearchResponse] | None = None, delay: float = 0) -> None:
        self.responses = responses or []
        self.delay = delay
        self.calls: list[str] = []

    async def search(self, query: str, max_results: int) -> SearchResponse:
        self.calls.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.responses:
            response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
            return response.model_copy(
                update={"query": query, "results": response.results[:max_results]}
            )
        return _response(query)


def test_query_generator_builds_quoted_site_and_topic_families() -> None:
    target = ResearchTarget(
        name="Acme AI",
        type=ResearchTargetType.COMPANY,
        aliases=["Acme Artificial Intelligence"],
        domains=["example.com"],
    )
    queries = QueryGenerator().generate(target)
    values = {item.query for item in queries}
    assert '"Acme AI"' in values
    assert 'site:example.com "Acme AI"' in values
    assert '"Acme AI" founder' in values
    assert len(values) == len(queries)


def test_follow_up_queries_use_founder_and_github_findings() -> None:
    target = ResearchTarget(name="Acme AI", type=ResearchTargetType.COMPANY)
    evidence = _response(
        "q",
        SearchResult(
            url="https://github.com/janesmith/acme",
            title="Jane Smith - Founder",
            snippet="Founder: Jane Smith; GitHub github.com/janesmith",
            source_domain="github.com",
        ),
    )
    from venture_intelligence.shared.models import ResearchEvidence

    item = ResearchEvidence(
        url=evidence.results[0].url,
        title=evidence.results[0].title,
        snippet=evidence.results[0].snippet,
        source_domain="github.com",
        query='"Acme AI"',
        target_name=target.name,
        confidence=0.6,
    )
    follow_ups = QueryGenerator().follow_ups(target, item, 1)
    assert any('"Jane Smith" "Acme AI"' in query.query for query in follow_ups)
    assert any("site:github.com" in query.query for query in follow_ups)
    assert is_probable_person_name("Jane Smith")
    assert not is_probable_person_name("Twitter Media")


def test_agent_respects_query_budget_and_json_contract() -> None:
    provider = FakeProvider()
    agent = ResearchAgent(provider, runtime_settings=Settings(research_cache_ttl_seconds=600))
    run = asyncio.run(
        agent.run(
            ResearchTarget(name="Acme AI", type=ResearchTargetType.COMPANY),
            ResearchBudget(
                max_queries=2, max_depth=0, max_results_per_query=1, max_runtime_seconds=5
            ),
        )
    )
    assert len(provider.calls) == 2
    assert len(run.queries) == 2
    assert json.loads(run.model_dump_json())["target"]["name"] == "Acme AI"
    assert run.stop_reason == "max_queries_reached"


def test_agent_caches_duplicate_queries() -> None:
    provider = FakeProvider()
    agent = ResearchAgent(provider, runtime_settings=Settings(research_cache_ttl_seconds=600))
    target = ResearchTarget(name="Acme AI", type=ResearchTargetType.COMPANY)
    budget = ResearchBudget(
        max_queries=1, max_depth=0, max_results_per_query=1, max_runtime_seconds=5
    )
    asyncio.run(agent.run(target, budget))
    asyncio.run(agent.run(target, budget))
    assert len(provider.calls) == 1


def test_agent_runtime_budget_degrades_to_structured_failure() -> None:
    provider = FakeProvider(delay=0.2)
    agent = ResearchAgent(provider, runtime_settings=Settings(research_cache_ttl_seconds=600))
    run = asyncio.run(
        agent.run(
            ResearchTarget(name="Slow Source", type=ResearchTargetType.COMPANY),
            ResearchBudget(
                max_queries=2, max_depth=0, max_results_per_query=1, max_runtime_seconds=0.02
            ),
        )
    )
    assert run.evidence == []
    assert run.sources[0].status == ResearchSourceStatus.UNAVAILABLE
    assert run.stop_reason in {"runtime_budget_exhausted", "max_queries_reached"}


def test_source_failures_are_classified_without_exceptions() -> None:
    assert (
        _status_for_result(CrawlResult("https://x", None, 401, "login"))
        == ResearchSourceStatus.AUTH_REQUIRED
    )
    assert (
        _status_for_result(CrawlResult("https://x", None, 403, "blocked"))
        == ResearchSourceStatus.BLOCKED
    )
    assert (
        _status_for_result(CrawlResult("https://x", None, 429, "rate limited"))
        == ResearchSourceStatus.RATE_LIMITED
    )
    assert (
        _status_for_result(CrawlResult("https://x", None, 404, "missing"))
        == ResearchSourceStatus.UNAVAILABLE
    )
    assert (
        _status_for_result(CrawlResult("https://x", None, 200, "redirected to login"))
        == ResearchSourceStatus.AUTH_REQUIRED
    )
    assert (
        _status_for_result(CrawlResult("https://x", "", 202))
        == ResearchSourceStatus.TEMPORARILY_UNAVAILABLE
    )


def test_duckduckgo_retries_202_without_asserting_evidence() -> None:
    class RetryCrawler:
        def __init__(self):
            self.calls = 0

        async def fetch_text(self, url: str) -> CrawlResult:
            self.calls += 1
            if self.calls < 3:
                return CrawlResult(url, "", 202, "accepted", {"Retry-After": "0"})
            return CrawlResult(
                url, '<a class="result__a" href="https://example.com">Example</a>', 200
            )

        async def close(self):
            return None

    crawler = RetryCrawler()
    response = asyncio.run(
        DuckDuckGoSearchProvider(crawler, max_retries=2, base_backoff_seconds=0).search("acme", 2)
    )
    assert crawler.calls == 3
    assert response.attempts == 3 and response.status == ResearchSourceStatus.AVAILABLE


def test_wayback_response_is_structured_historical_evidence() -> None:
    class ArchiveCrawler:
        async def fetch_text(self, url: str) -> CrawlResult:
            return CrawlResult(
                url,
                '[["timestamp","original","statuscode","mimetype"],["20200101","https://acme.test","200","text/html"]]',
                200,
            )

        async def close(self):
            return None

    response = asyncio.run(WaybackSearchProvider(ArchiveCrawler()).search("acme.test", 2))
    assert response.status == ResearchSourceStatus.AVAILABLE
    assert response.results[0].source_domain == "web.archive.org"
    assert response.results[0].published_at == "2020-01-01T00:00:00+00:00"
    historical = response.results[0].metadata["historical_observation"]
    assert historical["original_url"] == "https://acme.test"
    assert historical["temporal_status"] == "historical"
    assert historical["precision"] == "approximate"
    assert historical["lifecycle_signals"] == ["website_appeared"]
    assert response.results[0].provenance["provider"] == "internet-archive-cdx"


def test_arxiv_provider_uses_official_package_contract(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def results(self, search):
            assert search.query == "Acme AI"
            return iter(
                [
                    SimpleNamespace(
                        entry_id="http://arxiv.org/abs/1234.5678v2",
                        title="  Acme AI  ",
                        summary="An abstract.",
                        authors=[SimpleNamespace(name="Jane Doe")],
                        published=datetime(2026, 1, 2, tzinfo=timezone.utc),
                        updated=datetime(2026, 1, 3, tzinfo=timezone.utc),
                        categories=["cs.AI"],
                        primary_category="cs.AI",
                        journal_ref=None,
                        doi=None,
                        comment=None,
                        pdf_url="https://arxiv.org/pdf/1234.5678v2",
                        get_short_id=lambda: "1234.5678v2",
                    )
                ]
            )

    fake_module = SimpleNamespace(
        Client=FakeClient,
        Search=lambda **kwargs: SimpleNamespace(**kwargs),
        SortCriterion=SimpleNamespace(SubmittedDate="submittedDate"),
    )
    monkeypatch.setitem(__import__("sys").modules, "arxiv", fake_module)
    response = asyncio.run(ArxivSearchProvider(page_delay_seconds=0).search("Acme AI", 2))
    item = response.results[0]
    assert response.status == ResearchSourceStatus.AVAILABLE
    assert item.url == "https://arxiv.org/abs/1234.5678v2"
    assert item.discovery_method == "public_api"
    assert item.metadata["authors"] == ["Jane Doe"]
    assert item.provenance["provider"] == "arxiv.py"


def test_google_scholar_is_optional_and_structured_when_blocked() -> None:
    class FakeCrawler:
        def __init__(self, body):
            self.body = body

        async def fetch_text(self, url):
            return CrawlResult(url, self.body, 200)

        async def close(self):
            return None

    blocked = asyncio.run(
        GoogleScholarSearchProvider(FakeCrawler("captcha unusual traffic")).search("Acme", 3)
    )
    assert blocked.status == ResearchSourceStatus.BLOCKED and blocked.results == []
    html = (
        '<h3 class="gs_rt"><a href="https://example.org/paper?utm_source=scholar">'
        'Paper</a></h3><div class="gs_rs">Abstract text</div>'
    )
    response = asyncio.run(GoogleScholarSearchProvider(FakeCrawler(html)).search("Acme", 3))
    assert response.status == ResearchSourceStatus.AVAILABLE
    assert response.results[0].url == "https://example.org/paper"
    assert response.results[0].provenance["provider"] == "google-scholar-public-html"


def test_agent_fanout_keeps_provider_provenance_and_failures() -> None:
    class Provider:
        def __init__(self, domain, response):
            self.domain = domain
            self.response = response

        async def search(self, query, max_results):
            return self.response.model_copy(update={"query": query})

        async def close(self):
            return None

    arxiv_result = SearchResult(
        url="https://arxiv.org/abs/1234.5678",
        title="Acme AI research paper",
        snippet="Research by the Acme AI team.",
        source_domain="arxiv.org",
        discovery_method="public_api",
        direct_public=True,
        published_at="2026-01-02T00:00:00+00:00",
        provenance={"provider": "arxiv.py"},
    )
    agent = ResearchAgent(
        [
            Provider("duckduckgo.com", _response("q")),
            Provider("arxiv.org", _response("q", arxiv_result)),
        ],
        runtime_settings=Settings(research_cache_ttl_seconds=600),
    )
    run = asyncio.run(
        agent.run(
            ResearchTarget(name="Acme AI", type=ResearchTargetType.COMPANY),
            ResearchBudget(
                max_queries=1, max_depth=0, max_results_per_query=2, max_runtime_seconds=5
            ),
        )
    )
    assert {source.domain for source in run.sources} == {"duckduckgo.com", "arxiv.org"}
    assert run.evidence[0].discovery_method.value == "PUBLIC_API"
    assert run.evidence[0].provenance["provider"] == "arxiv.py"


def test_research_does_not_manufacture_specialized_or_generic_entities() -> None:
    provider = FakeProvider(
        [
            _response(
                "q",
                SearchResult(
                    url="https://x.com/twitter/media", title="media", source_domain="x.com"
                ),
            )
        ]
    )
    agent = ResearchAgent(provider, runtime_settings=Settings(research_cache_ttl_seconds=600))
    run = asyncio.run(
        agent.run(
            ResearchTarget(name="Twitter", type=ResearchTargetType.OTHER),
            ResearchBudget(
                max_queries=1, max_depth=0, max_results_per_query=1, max_runtime_seconds=5
            ),
        )
    )
    assert run.events_emitted == 0
    assert not is_probable_person_name("Twitter Media")


def test_uncorroborated_company_keyword_stays_candidate_only() -> None:
    provider = FakeProvider(
        responses=[
            _response(
                "q",
                SearchResult(
                    url="https://github.com/acme/repo",
                    title="Acme AI repository",
                    snippet="Source code for Acme AI",
                    source_domain="github.com",
                ),
            ),
            _response(
                "q",
                SearchResult(
                    url="https://arxiv.org/abs/1234.5678",
                    title="Acme AI paper",
                    snippet="A paper mentioning Acme AI",
                    source_domain="arxiv.org",
                ),
            ),
        ]
    )
    agent = ResearchAgent(provider, runtime_settings=Settings(research_cache_ttl_seconds=600))

    class RejectingBus:
        def publish(self, event):
            return False

    run = asyncio.run(
        agent.run(
            ResearchTarget(name="Acme AI", type=ResearchTargetType.COMPANY),
            ResearchBudget(
                max_queries=2, max_depth=0, max_results_per_query=1, max_runtime_seconds=5
            ),
            event_bus=RejectingBus(),
        )
    )
    assert run.entities == []
    assert run.events_emitted == 0
    assert any("identity-bearing" in item for item in run.next_actions)


def test_public_program_osint_is_bounded_and_provenance_preserving() -> None:
    class FakeCrawler:
        def __init__(self):
            self.calls = []

        async def fetch_text(self, url):
            self.calls.append(url)
            return CrawlResult(
                url,
                '<a class="result__a" href="https://www.linkedin.com/company/acme">Acme</a>',
                200,
            )

        async def close(self):
            return None

    crawler = FakeCrawler()
    response = asyncio.run(
        PublicProgramAnnouncementProvider(crawler, max_scoped_queries=2).search("climate", 2)
    )
    assert len(crawler.calls) == 2
    assert response.results[0].metadata["osint_layer"] == "indexed_public"
    assert response.results[0].provenance["access_policy"] == "indexed_public_only"


def test_name_only_independent_domains_do_not_corroborate_identity() -> None:
    provider = FakeProvider(
        responses=[
            _response(
                "q",
                SearchResult(
                    url="https://github.com/acme/repo",
                    title="Acme AI repository",
                    source_domain="github.com",
                ),
            ),
            _response(
                "q",
                SearchResult(
                    url="https://arxiv.org/abs/1234",
                    title="Acme AI paper",
                    source_domain="arxiv.org",
                ),
            ),
            _response(
                "q",
                SearchResult(
                    url="https://news.ycombinator.com/item?id=1",
                    title="Acme AI on Hacker News",
                    source_domain="news.ycombinator.com",
                ),
            ),
        ]
    )
    agent = ResearchAgent(provider, runtime_settings=Settings(research_cache_ttl_seconds=600))
    run = asyncio.run(
        agent.run(
            ResearchTarget(name="Acme AI", type=ResearchTargetType.COMPANY),
            ResearchBudget(
                max_queries=3, max_depth=0, max_results_per_query=1, max_runtime_seconds=5
            ),
        )
    )
    assert len({item.source_domain for item in run.evidence}) == 3
    assert run.confidence_label == "ambiguous"
    assert run.identity_status.value == "ambiguous"
    assert run.independent_evidence_origins == 0
    assert all(item.attribution_status.value == "unbound" for item in run.evidence)


def test_single_source_research_is_retained_as_evidence_not_promoted(tmp_path) -> None:
    provider = FakeProvider(
        [
            _response(
                "q",
                SearchResult(
                    url="https://github.com/acme/repo",
                    title="Acme AI repository",
                    source_domain="github.com",
                ),
            )
        ]
    )
    database = Database(str(tmp_path / "research.db"))
    agent = ResearchAgent(provider, runtime_settings=Settings(research_cache_ttl_seconds=600))
    run = asyncio.run(
        agent.run(
            ResearchTarget(name="Acme AI", type=ResearchTargetType.COMPANY),
            ResearchBudget(
                max_queries=1, max_depth=0, max_results_per_query=1, max_runtime_seconds=5
            ),
            event_bus=EventBus(database),
        )
    )
    events = database.get_events_for_entity("research__acme-ai")
    assert run.events_emitted == 0
    assert events == []
    assert run.evidence[0].url == "https://github.com/acme/repo"
    assert any("identity requires independent" in item for item in run.next_actions)
