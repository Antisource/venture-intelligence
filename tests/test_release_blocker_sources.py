"""Release-blocker regressions for search, feeds, VC signals, and defaults."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from venture_intelligence.agents.analyst import AnalystAgent
from venture_intelligence.api.app import app
from venture_intelligence.batch_discovery.engine import classify_result, discovery_queries
from venture_intelligence.batch_discovery.models import BatchDiscoveryRequest
from venture_intelligence.batch_discovery.repository import BatchDiscoveryRepository
from venture_intelligence.batch_discovery.service import BatchDiscoveryService
from venture_intelligence.config import Settings, settings
from venture_intelligence.crawler import CrawlResult
from venture_intelligence.pipeline.email import EmailNotificationService
from venture_intelligence.pipeline.graph import GraphBuilder
from venture_intelligence.programs import get_program_registry
from venture_intelligence.research.provider_policy import RSS_POLICY
from venture_intelligence.research.request_budget import RequestBudgetTracker
from venture_intelligence.research.sources import (
    BingSearchProvider,
    BraveSearchProvider,
    PublicWatchlistPageProvider,
    RssSearchProvider,
    SearchResponse,
    SearchResult,
    YahooSearchProvider,
    extract_venture_metadata,
    public_document_text,
)
from venture_intelligence.research.watchlist import (
    IntelligenceWatchlist,
    SourceStateStore,
    WatchlistEntry,
)
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EntityType,
    EventType,
    ResearchBudget,
    ResearchSourceStatus,
    Source,
    SourceOperationStatus,
)
from venture_intelligence.storage.db import Database


class _Crawler:
    def __init__(self, pages: dict[str, CrawlResult]) -> None:
        self.pages = pages

    async def fetch_text(self, url: str) -> CrawlResult:
        return self.pages[url]

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["watchlist", "verification"])
async def test_public_page_excerpts_exclude_code_before_truncation(
    tmp_path: Path, boundary: str,
) -> None:
    url = "https://acme.example/announcement"
    page = (
        '<html><head><style>' + '.keyframes { color: red; }' * 250 + '</style></head>'
        '<body><script>"Mallory Smith founded Fake Labs."</script>'
        '<template><p>Hidden founder Bob Jones</p></template>'
        '<article><p>Alice <strong>Chen</strong> founded Acme Labs.</p>'
        '<p>Alice Chen joined a16z Speedrun SR007 with Acme Labs.</p>'
        '<p>Research &amp; development.</p></article></body></html>'
    )

    class PageCrawler(_Crawler):
        async def fetch_text_budgeted(self, target, tracker, provider):
            return await self.fetch_text(target)

    crawler = PageCrawler({url: CrawlResult(url, page, 200, original_url=url)})
    if boundary == "watchlist":
        provider = PublicWatchlistPageProvider([
            WatchlistEntry(id="announcement", name="Public announcement",
                           kind="publication", public_urls=[url], roles=["DISCOVERY"]),
        ], crawler)
        response = await provider.search("founder announcement", 1)
        result = response.results[0]
    else:
        service = BatchDiscoveryService(
            BatchDiscoveryRepository(Database(str(tmp_path / "intel.db"))),
            [], crawler=crawler,
        )
        result = await service._verify_public_result(
            SearchResult(url=url, title="Public announcement"), None,
            RequestBudgetTracker(ResearchBudget()),
        )
    assert result is not None
    assert "Alice Chen founded Acme Labs." in result.snippet
    assert "Research & development." in result.snippet
    assert not any(term in result.snippet for term in ("keyframes", "Mallory", "Bob Jones"))
    observation, _, _ = classify_result(
        result=result, query="current founders", provider_id="public-page",
        run_id="html-regression", program=get_program_registry().get("speedrun_current"),
        observed_at=datetime.now(timezone.utc),
    )
    assert observation is not None
    assert observation.candidate_name == "Alice Chen"
    assert observation.company_name == "Acme Labs"
    assert observation.stance.value == "supports"


def test_separate_team_and_navigation_blocks_cannot_form_identity_claims() -> None:
    text = public_document_text(
        '<section><p>CEO, Co-founder</p><h3>Xan Wesley</h3>'
        '<p>Law @ Stanford</p></section>'
        '<footer><p>backed by</p><nav>Contact DRS Why Now Mobile</nav></footer>'
    )
    metadata = extract_venture_metadata("Public team", text, roles=["IDENTITY"])
    assert "investors" not in metadata
    observation, _, _ = classify_result(
        result=SearchResult(url="https://team.example/about", title="Public team",
                            snippet=text, direct_public=True),
        query="founders", provider_id="public-page", run_id="layout-boundaries",
        program=None, observed_at=datetime.now(timezone.utc),
    )
    assert observation is None  # A layout is not a prose identity assertion.


def test_public_image_descriptions_preserve_claims_without_joining_layout() -> None:
    text = public_document_text(
        '<script><img alt="Hidden founder Mallory Smith"></script>'
        '<template><img alt="Hidden founder Bob Jones"></template>'
        '<p>Alice Chen</p><img alt="CEO and Co-founder of Acme Labs">'
        '<img alt="Dana Lee, founder of Delta Labs">'
        '<img src="https://private.example/never-fetch" alt="" role="presentation">'
    )
    assert "Image description: Dana Lee, founder of Delta Labs" in text
    assert "Mallory" not in text and "Bob Jones" not in text
    assert "Alice Chen CEO" not in text
    observation, _, _ = classify_result(
        result=SearchResult(url="https://team.example/about", title="Public team",
                            snippet=text, direct_public=True),
        query="founders", provider_id="public-page", run_id="image-description",
        program=None, observed_at=datetime.now(timezone.utc),
    )
    assert observation is not None
    assert observation.candidate_name == "Dana Lee"
    assert observation.company_name == "Delta Labs"
    assert "Image description:" in observation.raw_observation["snippet"]


@pytest.mark.parametrize("role", ["CEO and Co-founder", "COO and co-founder", "CTO & Founder"])
def test_explicit_combined_founder_role_requires_same_clause_company(role: str) -> None:
    result = SearchResult(
        url="https://team.example/about", title="Public team",
        snippet=public_document_text(
            f'<img alt="Dana Lee, {role} of Delta Labs">'
            '<img alt="Delta Labs team badges for Speedrun SR007">'
        ), direct_public=True,
    )
    observation, _, _ = classify_result(
        result=result, query="founders", provider_id="public-page", run_id="combined-role",
        program=get_program_registry().get("speedrun_current"),
        observed_at=datetime.now(timezone.utc),
    )
    assert observation is not None
    assert observation.candidate_name == "Dana Lee"
    assert observation.company_name == "Delta Labs"
    assert observation.stance.value == "neutral"  # Badges do not assert membership.


@pytest.mark.parametrize("description", [
    "Dana Lee, CEO of Delta Labs", "Dana Lee; CEO and Co-founder of Delta Labs",
    "CEO and Co-founder; Dana Lee; Delta Labs",
])
def test_role_and_layout_do_not_invent_founder_relationship(description: str) -> None:
    observation, _, _ = classify_result(
        result=SearchResult(url="https://team.example/about", title="Public team",
                            snippet=description, direct_public=True),
        query="founders", provider_id="public-page", run_id="unattributed-role",
        program=None, observed_at=datetime.now(timezone.utc),
    )
    assert observation is None


def test_explicit_team_relationships_retain_only_same_company_cofounders() -> None:
    observation, _, _ = classify_result(
        result=SearchResult(url="https://team.example/about", title="Public team",
                            snippet=public_document_text(
                                '<img alt="Dana Lee, CEO and Co-founder of Delta Labs">'
                                '<img alt="Alice Chen, COO and Co-founder of Delta Labs">'
                                '<img alt="Bob Jones, CTO and Co-founder of Delta Labs">'
                                '<img alt="Other Person, founder of Different Labs">'
                                '<p>Founder</p><h3>Wrong Person</h3><p>Delta Labs</p>'
                            ), direct_public=True),
        query="founders", provider_id="public-page", run_id="explicit-team",
        program=None, observed_at=datetime.now(timezone.utc),
    )
    assert observation is not None
    assert observation.candidate_name == "Dana Lee"
    assert observation.company_name == "Delta Labs"
    assert observation.cofounders == ["Alice Chen", "Bob Jones"]
    assert observation.stance.value == "neutral"


def test_result_title_cannot_become_part_of_founder_name() -> None:
    observation, _, _ = classify_result(
        result=SearchResult(url="https://team.example/about", title="Founding Team",
                            snippet="Dana Lee, CEO and Co-founder of Delta Labs.",
                            direct_public=True),
        query="founders", provider_id="public-page", run_id="title-boundary",
        program=None, observed_at=datetime.now(timezone.utc),
    )
    assert observation is not None
    assert observation.candidate_name == "Dana Lee"
    assert observation.company_name == "Delta Labs"
    assert observation.cofounders == []


def test_explicit_role_first_founder_preserves_unicode_name() -> None:
    observation, _, _ = classify_result(
        result=SearchResult(url="https://news.example/studio", title="Studio announcement",
                            snippet=("DRS has joined a16z Speedrun SR007. "
                                     "Co-Founder and CEO of DRS, Osman Elsöz, said the "
                                     "company builds consumer applications."), direct_public=True),
        query="current founders", provider_id="public-page", run_id="role-first",
        program=get_program_registry().get("speedrun_current"),
        observed_at=datetime.now(timezone.utc),
    )
    assert observation is not None
    assert observation.candidate_name == "Osman Elsöz"
    assert observation.company_name == "DRS"
    assert observation.stance.value == "supports"


def test_explicit_turkish_program_acceptance_is_company_only_support() -> None:
    observation, _, _ = classify_result(
        result=SearchResult(
            url="https://news.example/drs-speedrun",
            title="Yerli girişim DRS, a16z speedrun programına kabul edildi",
            snippet=(
                "Yapay zeka odaklı uygulama stüdyosu DRS, a16z speedrun'ın "
                "SR007 dönemine kabul edildi. Ahmet Onur ile Osman Elsöz ve "
                "Talha Alver tarafından kurulan DRS büyümeyi hedefliyor."
            ),
            direct_public=True,
        ),
        query="current companies",
        provider_id="public-page",
        run_id="turkish-company-membership",
        program=get_program_registry().get("speedrun_current"),
        observed_at=datetime.now(timezone.utc),
    )
    assert observation is not None
    assert observation.candidate_name is None
    assert observation.company_name == "DRS"
    assert observation.stance.value == "supports"


@pytest.mark.asyncio
async def test_company_membership_corroborates_attributed_founder_without_inventing_person(
    tmp_path: Path,
) -> None:
    class SourceProvider:
        def __init__(self, provider_id: str, result: SearchResult) -> None:
            self.domain = f"{provider_id}.example"
            self.policy = SimpleNamespace(provider_id=provider_id)
            self.result = result

        def set_request_budget(self, _tracker: object) -> None:
            return None

        async def search(self, query: str, _max_results: int) -> SearchResponse:
            return SearchResponse(
                query=query,
                status=ResearchSourceStatus.AVAILABLE,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                results=[self.result],
            )

    roles = {"source_roles": ["IDENTITY", "VERIFICATION"]}
    service = BatchDiscoveryService(
        BatchDiscoveryRepository(Database(str(tmp_path / "corroborated-drs.db"))),
        [
            SourceProvider(
                "games-news",
                SearchResult(
                    url="https://games-news.example/drs-speedrun",
                    title="DRS joins a16z Speedrun accelerator",
                    snippet=(
                        "DRS has joined a16z Speedrun SR007. Co-Founder and CEO of "
                        "DRS, Osman Elsöz, said the company builds consumer applications."
                    ),
                    direct_public=True,
                    metadata=roles,
                ),
            ),
            SourceProvider(
                "startup-news",
                SearchResult(
                    url="https://startup-news.example/drs-speedrun",
                    title="Yerli girişim DRS, a16z speedrun programına kabul edildi",
                    snippet=(
                        "DRS, a16z speedrun'ın SR007 dönemine kabul edildi. Ahmet Onur "
                        "ile Osman Elsöz ve Talha Alver tarafından kurulan DRS büyüyor."
                    ),
                    direct_public=True,
                    metadata=roles,
                ),
            ),
        ],
    )
    run = await service.run(
        BatchDiscoveryRequest(
            program_id="speedrun_current",
            include_directory_verification=False,
            budget=ResearchBudget(max_queries=1, max_results_per_query=2, max_depth=0),
        ),
        observed_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        event_bus=EventBus(service.repository.database),
    )
    assert len(run.observations) == 2
    assert len(run.hypotheses) == 1
    candidate = run.hypotheses[0]
    assert candidate.candidate_name == "Osman Elsöz"
    assert candidate.company_name == "DRS"
    assert candidate.assessment.independent_supporting_sources == 2
    assert candidate.assessment.promotion_eligible is True
    assert run.promoted_count == 1
    assert all(
        item.candidate_name is None
        for item in run.observations
        if "startup-news" in item.source_domain
    )


@pytest.mark.parametrize("text", [
    "CEO of DRS, Osman Elsöz, said the company builds consumer apps.",
    "Co-Founder and CEO of DRS; Osman Elsöz; Consumer apps.",
    "Co-Founder and CEO; DRS; Osman Elsöz",
])
def test_role_first_requires_explicit_founder_company_person_relation(text: str) -> None:
    observation, _, _ = classify_result(
        result=SearchResult(url="https://news.example/studio", title="Studio announcement",
                            snippet=text, direct_public=True),
        query="founders", provider_id="public-page", run_id="role-first-negative",
        program=None, observed_at=datetime.now(timezone.utc),
    )
    assert observation is None


@pytest.mark.asyncio
async def test_attributed_team_survives_persistence_research_api_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "team-api.db"))
    url = "https://team.example/announcement"
    document = (
        '<p>Delta Labs has joined a16z Speedrun SR007.</p>'
        '<img alt="Dana Lee, CEO and Co-founder of Delta Labs">'
        '<img alt="Alice Chen, COO and Co-founder of Delta Labs">'
        '<img alt="Bob Jones, CTO and Co-founder of Delta Labs">'
        '<p>Founder</p><h3>Wrong Person</h3>'
    )
    provider = PublicWatchlistPageProvider([
        WatchlistEntry(id="team", name="Public team", kind="publication",
                       public_urls=[url], roles=["IDENTITY", "VERIFICATION"]),
    ], _Crawler({url: CrawlResult(url, document, 200)}))
    service = BatchDiscoveryService(
        BatchDiscoveryRepository(Database(settings.db_path)), [provider],
    )
    observed = datetime(2026, 9, 9, tzinfo=timezone.utc)
    run = await service.run(
        BatchDiscoveryRequest(program_id="speedrun_current", include_directory_verification=False,
                              budget=ResearchBudget(max_queries=2, max_depth=0)),
        observed_at=observed, event_bus=EventBus(service.repository.database),
    )
    assert len(run.observations) == len(run.hypotheses) == 1
    candidate = run.hypotheses[0]
    assert candidate.candidate_name == "Dana Lee"
    assert candidate.cofounders == candidate.profile.cofounders == ["Alice Chen", "Bob Jones"]
    assert candidate.assessment.independent_supporting_sources == 1
    assert candidate.assessment.promotion_eligible is False
    assert run.promoted_count == 0
    assert service.repository.database.get_all_entity_ids() == []
    with TestClient(app) as client:
        response = client.get(f"/api/v1/batch-discovery/{candidate.hypothesis_id}")
        assert response.status_code == 200
        evidence = response.json()["observations"]
        assert len(evidence) == 1
        assert evidence[0]["candidate_name"] == "Dana Lee"
        assert evidence[0]["cofounders"] == ["Alice Chen", "Bob Jones"]
        assert evidence[0]["provenance"]["evidence_origin"] == url
        assert "Image description:" in evidence[0]["claim"]
    before = service.repository.state_counts()
    assert service.replay("speedrun_current", observed - timedelta(seconds=1)).hypotheses == []
    replay = service.replay("speedrun_current", observed)
    assert replay.hypotheses[0].profile.cofounders == ["Alice Chen", "Bob Jones"]
    assert replay.model_dump() == service.replay("speedrun_current", observed).model_dump()
    assert service.repository.state_counts() == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_type", "request_url", "html", "expected"),
    [
        (
            BingSearchProvider,
            "https://www.bing.com/search?q=Acme",
            (
                '<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?'
                'u=a1aHR0cHM6Ly9hY21lLmV4YW1wbGUvbmV3cw">Acme News</a></h2></li>'
            ),
            "https://acme.example/news",
        ),
        (
            YahooSearchProvider,
            "https://search.yahoo.com/search?p=Acme",
            (
                '<a href="https://r.search.yahoo.com/_ylt=x/'
                'RU=https%3A%2F%2Facme.example%2Fnews/RK=2/RS=x">Acme News</a>'
            ),
            "https://acme.example/news",
        ),
        (
            BraveSearchProvider,
            "https://search.brave.com/search?q=Acme&source=web",
            '<a href="https://acme.example/news">Acme News</a>',
            "https://acme.example/news",
        ),
    ],
)
async def test_public_search_parsers_retain_acquisition_provenance(
    provider_type: type,
    request_url: str,
    html: str,
    expected: str,
) -> None:
    crawler = _Crawler({request_url: CrawlResult(request_url, html, 200, original_url=request_url)})
    provider = provider_type(crawler, max_retries=0)
    response = await provider.search("Acme", 3)
    assert response.operation_status is SourceOperationStatus.SUCCESS
    assert response.results[0].url == expected
    assert response.results[0].metadata["acquisition_provider"] == provider.policy.provider_id


@pytest.mark.asyncio
async def test_public_search_parser_preserves_indexed_snippet() -> None:
    request_url = "https://search.brave.com/search?q=Acme&source=web"
    html = (
        '<a href="https://acme.example/news">Acme News</a>'
        '<p class="snippet">Alice Chen founded Acme Labs.</p>'
    )
    provider = BraveSearchProvider(
        _Crawler({request_url: CrawlResult(request_url, html, 200, original_url=request_url)}),
        max_retries=0,
    )
    response = await provider.search("Acme", 3)
    assert response.results[0].snippet == "Alice Chen founded Acme Labs."


@pytest.mark.asyncio
@pytest.mark.parametrize("with_snippet", [True, False])
async def test_search_dedup_keeps_headline_and_evidence_not_breadcrumb(
    with_snippet: bool,
) -> None:
    request_url = "https://www.bing.com/search?q=SR007"
    target = "https://www.linkedin.com/posts/alice-announcement"
    claim = "Alice Chen is joining a16z Speedrun SR007 with Acme Labs."
    snippet = f'<div class="b_caption"><p>{claim}</p></div>' if with_snippet else ""
    document = (
        f'<li class="b_algo"><a href="{target}">linkedin.com / posts</a>'
        f'<h2><a href="{target}">Acme Labs founder announcement</a></h2>{snippet}</li>'
        '<li class="b_algo"><h2><a href="https://other.example/news">Other news</a></h2>'
        '<p class="snippet">Unrelated claim must not attach to Acme.</p></li>'
    )
    provider = BingSearchProvider(
        _Crawler({request_url: CrawlResult(request_url, document, 200)}), max_retries=0,
    )
    response = await provider.search("SR007", 1)
    assert len(response.results) == 1
    result = response.results[0]
    assert result.url == target
    assert result.title == "Acme Labs founder announcement"
    assert result.snippet == (claim if with_snippet else "")
    assert result.provenance["evidence_origin"] == target
    if with_snippet:
        observation, _, _ = classify_result(
            result=result, query="SR007", provider_id="bing", run_id="indexed-claim",
            program=get_program_registry().get("speedrun_current"),
            observed_at=datetime.now(timezone.utc),
        )
        assert observation is not None
        assert observation.candidate_name == "Alice Chen"
        assert observation.company_name == "Acme Labs"
        assert observation.stance.value == "supports"


def test_discovery_queries_start_generic_then_add_registry_terms() -> None:
    program = get_program_registry().get("yc_current")
    assert program is not None
    queries = discovery_queries(program)
    assert any("Y Combinator" not in query and "founder" in query for query in queries)
    assert any(program.name in query for query in queries)


def test_program_query_aliases_are_alternatives_not_simultaneous_requirements() -> None:
    program = get_program_registry().get("speedrun_current").model_copy(update={
        "name": "Example Accelerator", "batch": "B26", "keywords": ["EXA", "Example Program"],
    })
    query = discovery_queries(program)[0]
    assert '("Example Accelerator" OR "EXA" OR "Example Program") "B26"' in query
    assert "site:linkedin.com/posts" in query
    assert "site:x.com" in query


@pytest.mark.parametrize(("program_id", "batch_text", "expected"), [
    ("yc_current", "S26", "other_batch"),
    ("speedrun_current", "SR006", "other_batch"),
    ("yc_current", "", "neutral"),
    ("speedrun_current", "", "neutral"),
    ("yc_current", "S26 and F26", "ambiguous_identity"),
    ("speedrun_current", "SR006 and SR007", "ambiguous_identity"),
    ("yc_current", "F26", "supports"),
    ("speedrun_current", "SR007", "supports"),
])
def test_unstructured_cohort_claims_do_not_imply_current_membership(
    program_id: str, batch_text: str, expected: str,
) -> None:
    program = get_program_registry().get(program_id)
    observation, _, _ = classify_result(
        result=SearchResult(
            url="https://news.example/earlier-cohort",
            title="Founder Alice Chen announces cohort participation",
            snippet=f"Alice Chen joined {program.name} {batch_text} with Acme Labs.",
        ),
        query="current cohort", provider_id="bing", run_id="historical-negative",
        program=program, observed_at=datetime.now(timezone.utc),
    )
    assert observation is not None
    assert observation.stance.value == expected


@pytest.mark.parametrize(("program_id", "claim", "stance"), [
    ("speedrun_current",
     "Today, we announce that Lazyweb has joined a16z Speedrun SR007.", "supports"),
    ("speedrun_current", "Lazyweb has joined a16z Speedrun SR006.", "other_batch"),
    ("yc_current", "Lazyweb has joined Y Combinator F26.", "supports"),
])
def test_company_only_cohort_announcement_preserves_unknown_founder(
    program_id: str, claim: str, stance: str,
) -> None:
    observation, _, _ = classify_result(
        result=SearchResult(url="https://news.example/cohort", title="Company announcement",
                            snippet=claim),
        query="unknown current companies", provider_id="public-index", run_id="company-only",
        program=get_program_registry().get(program_id), observed_at=datetime.now(timezone.utc),
    )
    assert observation is not None
    assert observation.company_name == "Lazyweb"
    assert observation.candidate_name is None
    assert observation.candidate_kind.value == "company"
    assert observation.stance.value == stance


@pytest.mark.parametrize("label", ["Twitter", "GitHub", "Keyframes", "Arxiv"])
def test_generic_membership_subject_does_not_create_a_company(label: str) -> None:
    observation, _, _ = classify_result(
        result=SearchResult(url="https://news.example/cohort", title="Company announcement",
                            snippet=f"{label} has joined a16z Speedrun SR007."),
        query="unknown current companies", provider_id="public-index", run_id="generic-company",
        program=get_program_registry().get("speedrun_current"),
        observed_at=datetime.now(timezone.utc),
    )
    assert observation is None


@pytest.mark.parametrize(
    ("title", "snippet", "person", "company"),
    [
        ("Founder update", "Alice Chen is building Acme Labs.", "Alice Chen", "Acme Labs"),
        ("Acme Labs launches", "A new developer-tools startup launches.", None, "Acme Labs"),
        ("Founder update", "Founder Alice Chen is building in public.", "Alice Chen", None),
    ],
)
def test_provisional_extraction_preserves_credible_weak_hypotheses(
    title: str,
    snippet: str,
    person: str | None,
    company: str | None,
) -> None:
    program = get_program_registry().get("yc_current")
    assert program is not None
    observation, reason, relevant = classify_result(
        result=SearchResult(
            url="https://public.example/announcement",
            title=title,
            snippet=snippet,
            source_domain="public.example",
        ),
        query="generic venture discovery",
        provider_id="fixture-search",
        run_id="run",
        program=program,
        observed_at=datetime.now(timezone.utc),
    )
    assert relevant is True
    assert reason is None
    assert observation is not None
    assert observation.candidate_name == person
    assert observation.company_name == company
    assert observation.stance.value == "neutral"


def test_non_candidate_venture_context_is_not_relevant() -> None:
    program = get_program_registry().get("yc_current")
    assert program is not None
    observation, reason, relevant = classify_result(
        result=SearchResult(
            url="https://markets.example/report",
            title="Quarterly earnings and venture market index",
            snippet="Macroeconomic commentary about venture funding trends.",
            source_domain="markets.example",
        ),
        query="venture",
        provider_id="fixture-search",
        run_id="run",
        program=program,
        observed_at=datetime.now(timezone.utc),
    )
    assert observation is None
    assert relevant is False
    assert reason.value == "NOT_VENTURE_EVENT"


class _FixtureProvider:
    def __init__(self, provider_id: str, *, failed: bool = False) -> None:
        self.domain = f"{provider_id}.test"
        self.policy = SimpleNamespace(provider_id=provider_id)
        self.failed = failed

    def set_request_budget(self, _tracker: object) -> None:
        return None

    async def search(self, query: str, _max_results: int) -> SearchResponse:
        if self.failed:
            return SearchResponse(
                query=query,
                status=ResearchSourceStatus.TEMPORARILY_UNAVAILABLE,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                error="provider unavailable",
            )
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            results=[
                SearchResult(
                    url="https://acme.example/announcement",
                    title="Alice Chen joining Y Combinator S26 with Acme Labs",
                    snippet="Alice Chen is joining Y Combinator S26 with Acme Labs.",
                    source_domain="acme.example",
                    metadata={
                        "candidate_kind": "person",
                        "candidate_name": "Alice Chen",
                        "company_name": "Acme Labs",
                        "source_roles": ["DISCOVERY"],
                        "batch_claim": True,
                    },
                )
            ],
        )


@pytest.mark.asyncio
async def test_search_outage_falls_back_and_same_article_is_one_origin(
    tmp_path: Path,
) -> None:
    service = BatchDiscoveryService(
        BatchDiscoveryRepository(Database(str(tmp_path / "fallback.db"))),
        [
            _FixtureProvider("duckduckgo", failed=True),
            _FixtureProvider("bing"),
            _FixtureProvider("brave"),
        ],
    )
    run = await service.run(
        BatchDiscoveryRequest(
            program_id="yc_current",
            budget=ResearchBudget(
                max_queries=1,
                max_results_per_query=3,
                max_runtime_seconds=5,
                max_total_requests=10,
                max_requests_per_provider=5,
                max_retry_requests=1,
                max_requests_per_domain=5,
            ),
        )
    )
    assert len(run.observations) == 1
    assert run.observations[0].acquisition_providers == ["bing", "brave"]
    assert len(run.observations[0].provenance["acquisition_paths"]) == 2
    statuses = {item.provider_id: item.status for item in run.source_outcomes}
    assert statuses["duckduckgo"] is SourceOperationStatus.TEMPORARILY_UNAVAILABLE
    assert statuses["bing"] is SourceOperationStatus.SUCCESS


@pytest.mark.asyncio
async def test_unscoped_venture_discovery_requires_no_accelerator_selection(
    tmp_path: Path,
) -> None:
    service = BatchDiscoveryService(
        BatchDiscoveryRepository(Database(str(tmp_path / "generic.db"))),
        _FixtureProvider("bing"),
    )
    run = await service.run(
        BatchDiscoveryRequest(
            budget=ResearchBudget(
                max_queries=1,
                max_results_per_query=3,
                max_runtime_seconds=5,
                max_total_requests=3,
                max_requests_per_provider=3,
                max_retry_requests=0,
                max_requests_per_domain=3,
            )
        ),
        event_bus=EventBus(service.repository.database),
    )
    assert run.program_id == "generic_venture"
    assert run.batch_id == "unscoped"
    assert len(run.hypotheses) == 1
    assert run.hypotheses[0].assessment.promotion_eligible is False
    assert run.promoted_count == 0


@pytest.mark.asyncio
async def test_rss_discovery_does_not_require_only_the_quoted_or_alternative() -> None:
    url = "https://news.example/feed"
    xml = """<rss><channel><item>
      <title>Acme Labs raises $5M seed round</title>
      <link>https://news.example/acme</link>
      <description>Startup founded by Alice Chen announced funding.</description>
    </item><item>
      <title>Local weather forecast</title>
      <link>https://news.example/weather</link>
      <description>Sunny and warm throughout the week.</description>
    </item></channel></rss>"""
    provider = RssSearchProvider([url], _Crawler({url: CrawlResult(url, xml, 200)}))
    for query in discovery_queries(None):
        result = await provider.search(query, 5)
        assert [item.url for item in result.results] == ["https://news.example/acme"]
    assert (await provider.search('"Different Company"', 5)).results == []


@pytest.mark.parametrize("program_id", [None, "yc_current", "speedrun_current"])
@pytest.mark.asyncio
async def test_blind_discovery_excludes_all_configured_directory_hosts(
    tmp_path: Path, program_id: str | None,
) -> None:
    class Provider(_FixtureProvider):
        async def search(self, query: str, max_results: int) -> SearchResponse:
            response = await super().search(query, max_results)
            sample = response.results[0]
            response.results = [sample.model_copy(update={"url": url}) for url in (
                "https://www.ycombinator.com/companies/acme",
                "https://speedrun.a16z.com/companies/acme",
            )]
            return response

    service = BatchDiscoveryService(
        BatchDiscoveryRepository(Database(str(tmp_path / "blind.db"))), Provider("bing")
    )
    run = await service.run(BatchDiscoveryRequest(
        program_id=program_id, include_directory_verification=False,
        budget=ResearchBudget(max_queries=1),
    ))
    assert run.funnel.retrieved_items == 2
    assert run.observations == []
    assert run.hypotheses == []
    assert service.repository.database.get_all_entity_ids() == []


def test_dictionary_founder_definition_is_not_a_venture_verification_target() -> None:
    observation, reason, relevant = classify_result(
        result=SearchResult(
            url="https://dictionary.example/founder",
            title="Founder Definition & Meaning",
            snippet="A founder is a person who establishes a company or startup.",
        ),
        query=discovery_queries(None)[0], provider_id="bing", run_id="diagnostic",
        program=None, observed_at=datetime.now(timezone.utc),
    )
    assert observation is None
    assert reason is not None
    assert relevant is False


@pytest.mark.asyncio
async def test_unscoped_discovery_replay_uses_observation_cutoff_without_live_mutation(
    tmp_path: Path,
) -> None:
    service = BatchDiscoveryService(
        BatchDiscoveryRepository(Database(str(tmp_path / "replay.db"))), _FixtureProvider("bing")
    )
    observed = datetime(2026, 9, 8, tzinfo=timezone.utc)
    run = await service.run(
        BatchDiscoveryRequest(budget=ResearchBudget(max_queries=1)), observed_at=observed
    )
    before = service.repository.state_counts()
    early = service.replay(None, observed - timedelta(seconds=1))
    current = service.replay(None, observed)
    repeated = service.replay(None, observed)
    assert early.observations_selected == 0
    assert early.hypotheses == []
    assert current.program_id == "generic_venture"
    assert current.batch_id == "unscoped"
    assert current.observations_selected == len(run.observations) == 1
    assert current.hypotheses[0].company_name == "Acme Labs"
    assert current.model_dump() == repeated.model_dump()
    assert service.repository.state_counts() == before
    assert service.repository.database.get_all_entity_ids() == []


def test_historical_speedrun_batch_resolves_without_changing_current_target() -> None:
    registry = get_program_registry()
    current = registry.get("speedrun_current")
    historical = registry.batch("speedrun_current", "SR006")
    assert historical is not None
    assert historical.historical is True
    assert current is not None
    assert current.current_batch == "SR007"
    assert current.directory_url == (
        "https://speedrun-api.a16z.com/api/companies/companies/?cohort=SR007&limit=100"
    )
    assert registry.batch("speedrun_current", "SR007").directory_url == current.directory_url


@pytest.mark.asyncio
async def test_unscoped_candidate_listing_and_research_api_preserve_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "unscoped-api.db"))
    service = BatchDiscoveryService(
        BatchDiscoveryRepository(Database(settings.db_path)), _FixtureProvider("bing")
    )
    run = await service.run(BatchDiscoveryRequest(budget=ResearchBudget(max_queries=1)))
    with TestClient(app) as client:
        listing = client.get("/api/v1/batch-discovery")
        assert listing.status_code == 200
        assert listing.json()["program_id"] == "generic_venture"
        assert len(listing.json()["candidates"]) == 1
        candidate_id = listing.json()["candidates"][0]["hypothesis_id"]
        detail = client.get(f"/api/v1/batch-discovery/{candidate_id}")
        assert detail.status_code == 200
        assert detail.json()["observations"] == [
            item.model_dump(mode="json") for item in run.observations
        ]
        scoped = client.get("/api/v1/batch-discovery?program_id=yc_current")
        assert scoped.status_code == 200
        assert scoped.json()["candidates"] == []
        assert client.get("/api/v1/batch-discovery?program_id=missing").status_code == 404


@pytest.mark.asyncio
async def test_incremental_rss_is_successful_empty_and_extracts_funding(
    tmp_path: Path,
) -> None:
    url = "https://news.example/feed"
    xml = """<?xml version="1.0"?><rss><channel><item>
      <title>Acme Labs raises $5M seed round</title>
      <link>https://news.example/acme</link>
      <description>Acme Labs, founded by Alice Chen, raised funding
      led by Sequoia Capital.</description>
      <pubDate>2026-08-20T10:00:00+00:00</pubDate>
    </item></channel></rss>"""
    state = SourceStateStore(tmp_path / "state.json")
    metadata = {
        url: {
            "id": "news-feed",
            "name": "News Feed",
            "organization": "News Example",
            "roles": ["DISCOVERY", "FUNDING", "INVESTOR_SIGNAL", "TEMPORAL_NEWS"],
        }
    }
    first = RssSearchProvider(
        [url],
        _Crawler({url: CrawlResult(url, xml, 200, original_url=url)}),
        provider_id="newsletter_watchlist",
        policy=RSS_POLICY,
        state_store=state,
        source_metadata=metadata,
    )
    response = await first.search('"Acme Labs"', 5)
    assert response.operation_status is SourceOperationStatus.SUCCESS
    assert response.results[0].metadata["funding_amount"] == "$5M"
    assert response.results[0].metadata["lead_investor"] == "Sequoia Capital"
    assert response.results[0].metadata["candidate_name"] == "Alice Chen"

    second = RssSearchProvider(
        [url],
        _Crawler({url: CrawlResult(url, xml, 200, original_url=url)}),
        provider_id="newsletter_watchlist",
        policy=RSS_POLICY,
        state_store=state,
        source_metadata=metadata,
    )
    empty = await second.search('"Acme Labs"', 5)
    assert empty.results == []
    assert empty.status is ResearchSourceStatus.AVAILABLE
    assert empty.operation_status is SourceOperationStatus.EMPTY


def test_funding_graph_edges_are_evidence_backed(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "graph.db"))
    event = CanonicalEvent(
        event_type=EventType.FUNDING_ANNOUNCED,
        entity_type=EntityType.COMPANY,
        entity_id="fixture__acme",
        source=Source.RESEARCH_AGENT,
        source_url="https://news.example/acme",
        occurred_at=datetime.now(timezone.utc),
        confidence=0.82,
        raw_payload={
            "name": "Acme Labs",
            "amount": "$5M",
            "round": "seed",
            "investors": ["Sequoia Capital"],
            "lead_investor": "Sequoia Capital",
        },
    )
    GraphBuilder(database).process(event, "company__acme")
    with database._connect() as connection:
        edge = connection.execute(
            "SELECT edge_type, evidence_ids FROM graph_edges WHERE target_id=?",
            ("company__acme",),
        ).fetchone()
    assert edge["edge_type"] == "LED_INVESTMENT_IN"
    assert event.event_id in edge["evidence_ids"]


def test_default_source_settings_and_fresh_email_are_truthful(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "source_settings_path", str(tmp_path / "sources.json"))
    monkeypatch.setattr(settings, "source_state_path", str(tmp_path / "state.json"))
    monkeypatch.setattr(settings, "source_quality_report_path", str(tmp_path / "quality.json"))
    with TestClient(app) as client:
        response = client.get("/api/v1/sources")
        assert response.status_code == 200
        payload = response.json()
        a16z = next(item for item in payload["watchlists"] if item["id"] == "a16z-news")
        assert a16z["organization"] == "Andreessen Horowitz"
        assert a16z["aliases"] == ["a16z"]
        runtime = {item["source_id"]: item for item in payload["runtime"]}
        for source_id in (
            "duckduckgo",
            "bing",
            "yahoo",
            "brave",
            "techcrunch",
            "newsletter_watchlist",
            "vc_watchlist",
        ):
            assert runtime[source_id]["enabled"] is True
            assert runtime[source_id]["health"] == "not_checked"
        assert client.put("/api/v1/sources/bing", json={"enabled": False}).status_code == 200
        assert {
            item["source_id"]: item for item in client.get("/api/v1/sources").json()["runtime"]
        }["bing"]["enabled"] is False
    email = EmailNotificationService(tmp_path / "email.json")
    assert email.load()["destination"] is None
    assert email.preview("Digest", "No changes")["delivery_configured"] is False


def test_public_watchlist_resolves_a16z_as_generic_alias() -> None:
    watchlist = IntelligenceWatchlist.load("config/intelligence_watchlists.yaml")

    canonical = watchlist.resolve("Andreessen Horowitz", "vc_firm")
    alias = watchlist.resolve("a16z", "vc_firm")

    assert canonical is not None
    assert alias is not None
    assert canonical.id == alias.id == "a16z-news"


@pytest.mark.asyncio
async def test_analyst_auto_mode_falls_back_without_hosted_or_local_model() -> None:
    runtime = Settings(
        _env_file=None,
        analyst_enabled=True,
        analyst_provider="auto",
        ollama_base_url="http://127.0.0.1:1",
        ollama_timeout_seconds=0.05,
    )
    result = await AnalystAgent(runtime).answer(
        "What deserves attention?",
        {
            "candidates": [
                {
                    "display_name": "Acme Labs",
                    "confidence_pct": 82,
                    "state": "corroborated",
                }
            ]
        },
    )
    assert result.model == "local-boundary"
    assert result.evidence_sufficient is True
    assert "Acme Labs" in result.answer
