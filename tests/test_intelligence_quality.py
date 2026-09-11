"""Pre-UI intelligence quality, lineage, and analyst-trust regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from venture_intelligence.agents.analyst import (
    AnalystAgent,
    AnalystResult,
    EpistemicState,
    LocalBoundaryProvider,
)
from venture_intelligence.batch_discovery.engine import (
    build_hypotheses,
    cluster_observations,
    mark_source_independence,
    observation_from_result,
)
from venture_intelligence.batch_discovery.models import (
    BatchDiscoveryRequest,
    BatchDiscoveryRun,
    BatchSourceOutcome,
    CandidateState,
    DiscoveryDropReason,
    DiscoveryStage,
    DiscoveryZeroState,
)
from venture_intelligence.batch_discovery.repository import BatchDiscoveryRepository
from venture_intelligence.batch_discovery.service import BatchDiscoveryService
from venture_intelligence.config import Settings
from venture_intelligence.operations.cycle import IntelligenceCycleService
from venture_intelligence.programs import get_program_registry
from venture_intelligence.research.sources import (
    SearchResponse,
    SearchResult,
    extract_venture_metadata,
)
from venture_intelligence.shared.models import (
    CRMActionType,
    ResearchBudget,
    ResearchSourceStatus,
    SourceOperationStatus,
)
from venture_intelligence.storage.db import Database

UTC = timezone.utc
NOW = datetime(2026, 9, 1, 9, tzinfo=UTC)


def _budget() -> ResearchBudget:
    return ResearchBudget(
        max_queries=18,
        max_depth=2,
        max_results_per_query=10,
        max_runtime_seconds=30,
        max_total_requests=30,
        max_requests_per_provider=30,
        max_retry_requests=2,
        max_requests_per_domain=10,
    )


class WeakVentureProvider:
    domain = "bing"

    def __init__(self) -> None:
        self.calls = 0

    def set_request_budget(self, _tracker: object) -> None:
        return None

    async def search(self, query: str, _max_results: int) -> SearchResponse:
        self.calls += 1
        results: list[SearchResult] = []
        if self.calls == 1:
            metadata = extract_venture_metadata(
                "Nova Forge raised a $6M seed round",
                "Nova Forge launched its developer platform after closing the round.",
                roles=["DISCOVERY", "FUNDING", "MARKET_SIGNAL"],
                publication="Independent Startup Wire",
            )
            results = [
                SearchResult(
                    url="https://startupwire.example/nova-forge-seed",
                    title="Nova Forge raised a $6M seed round",
                    snippet="Nova Forge launched its developer platform after closing the round.",
                    source_domain="startupwire.example",
                    published_at="2026-08-30T12:00:00+00:00",
                    metadata=metadata,
                ),
                SearchResult(
                    url="https://example.com/weather",
                    title="Weekly weather report",
                    snippet="Clear skies are expected.",
                    source_domain="example.com",
                ),
                SearchResult(
                    url="https://news.example/stealth-founder",
                    title="Founder starts something new",
                    snippet="A founder launched a stealth startup but no identity is disclosed.",
                    source_domain="news.example",
                ),
            ]
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE,
            results=results,
            retrieved_at=NOW.isoformat(),
        )


@pytest.mark.asyncio
async def test_weak_venture_signal_survives_with_explainable_funnel(tmp_path: Path) -> None:
    service = BatchDiscoveryService(
        BatchDiscoveryRepository(Database(str(tmp_path / "weak.db"))),
        WeakVentureProvider(),
    )
    run = await service.run(
        BatchDiscoveryRequest(program_id="yc_current", budget=_budget()),
        observed_at=NOW,
    )

    candidate = next(item for item in run.hypotheses if item.company_name == "Nova Forge")
    assert candidate.assessment.batch_evidence_present is False
    assert candidate.assessment.accelerator_investigation_state == "needs_accelerator_investigation"
    assert candidate.assessment.promotion_eligible is False
    assert candidate.profile.investment_attention_pct >= 35
    assert candidate.profile.financial_claims[0].claim_kind.value == "round_amount"
    assert "no_target_program_or_batch_evidence" in candidate.assessment.promotion_blockers
    assert run.funnel.retrieved_items == 3
    assert run.funnel.relevant_items == 2
    assert run.funnel.venture_observations == 1
    assert run.funnel.candidate_hypotheses_created == 1
    assert run.funnel.batch_evidence_present == 0
    assert run.funnel.canonical_promotions == 0
    assert run.funnel.drop_reasons[DiscoveryDropReason.IRRELEVANT_TO_VENTURE] == 1
    assert run.funnel.drop_reasons[DiscoveryDropReason.NO_ENTITY_MENTION] == 1
    assert run.funnel.drop_reasons[DiscoveryDropReason.NO_BATCH_EVIDENCE] == 1
    assert run.zero_state is DiscoveryZeroState.THRESHOLD_ZERO
    replayed = service.replay("yc_current", NOW)
    replayed_candidate = next(
        item for item in replayed.hypotheses if item.company_name == "Nova Forge"
    )
    assert (
        replayed_candidate.assessment.source_coverage.model_dump()
        == candidate.assessment.source_coverage.model_dump()
    )


def _observation(
    url: str,
    *,
    provider: str,
    stance: str,
    roles: list[str],
    reliability: float = 0.9,
    metadata_extra: dict[str, object] | None = None,
):
    program = get_program_registry().get("yc_current")
    assert program is not None
    metadata = {
        "candidate_kind": "company",
        "company_name": "Coverage Labs",
        "program_id": program.id,
        "batch_id": program.batch,
        "stance": stance,
        "source_roles": roles,
        "source_reliability": reliability,
        "identity_confidence": 0.9,
        "directness": 0.9,
        "temporal_relevance": 0.9,
        **(metadata_extra or {}),
    }
    result = SearchResult(
        url=url,
        title="Coverage Labs accelerator investigation",
        snippet="Coverage Labs is being investigated for the target program.",
        source_domain=url.split("/", 3)[2],
        metadata=metadata,
    )
    observation = observation_from_result(
        result=result,
        query="coverage",
        provider_id=provider,
        run_id="coverage-run",
        program=program,
        observed_at=NOW,
    )
    assert observation is not None
    return observation


def test_source_coverage_uses_applicable_and_successfully_checked_denominator() -> None:
    observations = mark_source_independence(
        [
            _observation(
                "https://accelerator.example/coverage",
                provider="official",
                stance="supports",
                roles=["VERIFICATION"],
                reliability=0.97,
            ),
            _observation(
                "https://coverage.example/about",
                provider="website",
                stance="supports",
                roles=["VERIFICATION"],
                metadata_extra={"official_website": True},
            ),
            _observation(
                "https://news.example/coverage",
                provider="bing",
                stance="supports",
                roles=["DISCOVERY"],
            ),
            _observation(
                "https://x.com/ambiguous/status/1",
                provider="brave",
                stance="contradicts",
                roles=["DISCOVERY"],
            ),
        ]
    )
    program = get_program_registry().get("yc_current")
    assert program is not None
    candidate = build_hypotheses(
        observations,
        run_id="coverage-run",
        program=program,
        revision_for=lambda _value: 1,
    )[0]
    run = BatchDiscoveryRun(
        program_id=program.id,
        batch_id=program.batch,
        budget=_budget(),
        source_outcomes=[
            BatchSourceOutcome(
                provider_id="techcrunch",
                source_family="techcrunch",
                stage=DiscoveryStage.DISCOVERY,
                status=SourceOperationStatus.EMPTY,
            ),
            BatchSourceOutcome(
                provider_id="newsletter_watchlist",
                source_family="newsletter_substack",
                stage=DiscoveryStage.DISCOVERY,
                status=SourceOperationStatus.CIRCUIT_OPEN,
            ),
            BatchSourceOutcome(
                provider_id="vc_watchlist",
                source_family="vc_partner",
                stage=DiscoveryStage.DISCOVERY,
                status=SourceOperationStatus.PERMISSION_REQUIRED,
            ),
        ],
    )
    covered = BatchDiscoveryService._with_run_coverage(candidate, run)
    coverage = covered.assessment.source_coverage
    assert covered.assessment.confidence_pct == candidate.assessment.confidence_pct
    assert coverage.applicable_families == 8
    assert coverage.checked_families == 5
    assert coverage.coverage_pct == 62.5
    assert coverage.supporting_families == 3
    assert coverage.contradicting_families == 1
    assert coverage.unavailable_families == 1
    assert coverage.permission_required_families == 1
    assert {
        item.family for item in coverage.families if not item.applicable
    } == {"technical_publication", "historical_archive"}


def test_high_confidence_can_coexist_with_low_coverage() -> None:
    program = get_program_registry().get("yc_current")
    assert program is not None
    observations = mark_source_independence(
        [
            _observation(
                "https://accelerator.example/high-confidence",
                provider="official",
                stance="supports",
                roles=["IDENTITY", "VERIFICATION"],
                reliability=0.98,
            ),
            _observation(
                "https://company.example/program-announcement",
                provider="website",
                stance="supports",
                roles=["IDENTITY", "VERIFICATION"],
                reliability=0.94,
            ),
        ]
    )
    candidate = build_hypotheses(
        observations,
        run_id="high-low",
        program=program,
        revision_for=lambda _value: 1,
    )[0]
    assert candidate.assessment.confidence_pct >= 72
    assert candidate.assessment.source_coverage.coverage_pct < 50
    assert candidate.assessment.coverage_label == "HIGH CONFIDENCE · LOW COVERAGE"


def test_acquisition_paths_do_not_multiply_origins_and_reposts_are_derivative() -> None:
    program = get_program_registry().get("yc_current")
    assert program is not None
    base_metadata = {
        "candidate_kind": "company",
        "company_name": "Acme",
        "program_id": program.id,
        "batch_id": program.batch,
        "stance": "supports",
        "source_roles": ["DISCOVERY", "FUNDING"],
    }

    def make(url: str, provider: str, extra: dict[str, object] | None = None):
        result = SearchResult(
            url=url,
            title="Acme raised a seed round",
            snippet="Acme raised a $6M seed round.",
            source_domain=url.split("/", 3)[2],
            metadata={**base_metadata, **(extra or {})},
        )
        value = observation_from_result(
            result=result,
            query="Acme funding",
            provider_id=provider,
            run_id="origins",
            program=program,
            observed_at=NOW,
        )
        assert value is not None
        return value

    tech_bing = make("https://techcrunch.com/acme-seed", "bing")
    tech_brave = make("https://techcrunch.com/acme-seed", "brave")
    tech_ddg = make("https://techcrunch.com/acme-seed", "duckduckgo")
    merged = BatchDiscoveryService._merge_observation(tech_bing, tech_brave)
    merged = BatchDiscoveryService._merge_observation(merged, tech_ddg)
    newsletter = make(
        "https://newsletter.example/acme",
        "newsletter_watchlist",
        {"referenced_source_url": "https://techcrunch.com/acme-seed"},
    )
    repost = make(
        "https://vc.example/blog/acme-repost",
        "vc_watchlist",
        {"referenced_source_url": "https://techcrunch.com/acme-seed"},
    )
    portfolio = make(
        "https://vc.example/portfolio/acme",
        "vc_watchlist",
        {"investors": ["Fund X"], "investor_relationship": "portfolio_company"},
    )
    marked = mark_source_independence([merged, newsletter, repost, portfolio])
    assert merged.acquisition_providers == ["bing", "brave", "duckduckgo"]
    assert len(merged.provenance["acquisition_paths"]) == 3
    assert sum(item.independence.value == "independent" for item in marked) == 2
    assert sum(item.independence.value == "derivative" for item in marked) == 2


@pytest.mark.parametrize(
    ("text", "claim_kind", "status", "round_type", "relationship"),
    [
        ("Acme raised a $6M seed round.", "round_amount", "announced", "Seed", None),
        ("Acme has raised $12M in total.", "total_funding", "reported", "UNKNOWN", None),
        ("Acme is targeting a $10M Series A.", "target_raise", "targeting", "Series A", None),
        ("Acme was valued at $80M.", "valuation", "reported", "UNKNOWN", None),
        ("Fund X announced a new $500M vehicle.", "fund_size", "announced", "UNKNOWN", None),
        ("Partner Jane Doe said she was excited about Acme.", None, None, None, "mentioned"),
        ("Partner Jane Doe led Acme's seed round.", None, None, None, "led"),
        ("Fund X participated in Acme's round.", None, None, None, "participated"),
        ("TechCrunch reports that investors include X and Y.", None, None, None, "invested"),
    ],
)
def test_funding_claim_corpus_preserves_semantics(
    text: str,
    claim_kind: str | None,
    status: str | None,
    round_type: str | None,
    relationship: str | None,
) -> None:
    metadata = extract_venture_metadata(text, text, roles=["DISCOVERY", "FUNDING"])
    assert metadata.get("funding_claim_kind") == claim_kind
    assert metadata.get("funding_status") == status
    assert metadata.get("round_type") == round_type
    assert metadata.get("investor_relationship") == relationship
    if claim_kind == "target_raise":
        assert metadata["funding_status"] != "announced"
    if claim_kind in {"valuation", "fund_size", "revenue", "total_funding"}:
        assert "funding_round" not in metadata


def test_conflicting_round_amounts_are_preserved_not_silently_resolved() -> None:
    program = get_program_registry().get("yc_current")
    assert program is not None
    observations = []
    for amount, url in (
        ("$5M", "https://source-a.example/acme-seed"),
        ("$6M", "https://source-b.example/acme-seed"),
    ):
        result = SearchResult(
            url=url,
            title=f"Acme raised a {amount} seed round",
            snippet=f"Acme raised a {amount} seed round.",
            source_domain=url.split("/", 3)[2],
            metadata={
                "candidate_kind": "company",
                "company_name": "Acme",
                "program_id": program.id,
                "batch_id": program.batch,
                "stance": "neutral",
                "source_roles": ["DISCOVERY", "FUNDING"],
                "funding_amount": amount,
                "funding_claim_kind": "round_amount",
                "funding_status": "reported",
                "funding_round": "Seed",
                "round_type": "Seed",
            },
        )
        observation = observation_from_result(
            result=result,
            query="Acme seed",
            provider_id="fixture",
            run_id="conflict",
            program=program,
            observed_at=NOW,
        )
        assert observation is not None
        observations.append(observation)
    candidate = build_hypotheses(
        mark_source_independence(observations),
        run_id="conflict",
        program=program,
        revision_for=lambda _value: 1,
    )[0]
    claims = candidate.profile.financial_claims
    assert {item.amount for item in claims} == {"$5M", "$6M"}
    assert all(item.conflicts_with for item in claims)


def test_identity_stress_avoids_false_merges_and_preserves_explicit_rename() -> None:
    same_name_a = _observation(
        "https://one.example/alex",
        provider="bing",
        stance="neutral",
        roles=["IDENTITY"],
        metadata_extra={"candidate_name": "Alex Kim", "company_name": "One Labs"},
    )
    same_name_b = _observation(
        "https://two.example/alex",
        provider="bing",
        stance="neutral",
        roles=["IDENTITY"],
        metadata_extra={"candidate_name": "Alex Kim", "company_name": "Two Labs"},
    )
    collision_us = _observation(
        "https://north.example/nova",
        provider="bing",
        stance="neutral",
        roles=["DISCOVERY"],
        metadata_extra={"company_name": "Nova", "country": "US"},
    )
    collision_in = _observation(
        "https://south.example/nova",
        provider="bing",
        stance="neutral",
        roles=["DISCOVERY"],
        metadata_extra={"company_name": "Nova", "country": "IN"},
    )
    stealth = _observation(
        "https://founder.example/stealth",
        provider="bing",
        stance="neutral",
        roles=["IDENTITY"],
        metadata_extra={
            "candidate_name": "Rae Singh",
            "company_name": "Stealth ABC",
            "domains": ["acme.ai"],
        },
    )
    public = _observation(
        "https://acme.ai/about",
        provider="bing",
        stance="neutral",
        roles=["IDENTITY"],
        metadata_extra={
            "candidate_name": "Rae Singh",
            "company_name": "Acme AI",
            "domains": ["acme.ai"],
            "aliases": ["Stealth ABC"],
        },
    ).model_copy(
        update={
            "observed_at": NOW + timedelta(days=2),
            "retrieved_at": NOW + timedelta(days=2),
        }
    )
    clusters = cluster_observations(
        [same_name_a, same_name_b, collision_us, collision_in, stealth, public]
    )
    assert len(clusters) == 5
    rename_cluster = next(
        items
        for items in clusters.values()
        if {item.company_name for item in items} >= {"Stealth ABC", "Acme AI"}
    )
    assert len(rename_cluster) == 2
    program = get_program_registry().get("yc_current")
    assert program is not None
    renamed = build_hypotheses(
        mark_source_independence(rename_cluster),
        run_id="identity",
        program=program,
        revision_for=lambda _value: 1,
    )[0]
    assert renamed.company_name == "Acme AI"
    assert "Stealth ABC" in renamed.aliases
    assert renamed.first_seen_at == NOW


@pytest.mark.parametrize(
    ("left_extra", "right_extra"),
    [
        # A nickname/full-name resemblance is not an identity proof.
        (
            {"candidate_name": "Alex Kim", "company_name": "One Labs"},
            {"candidate_name": "Alexander Kim", "company_name": "One Labs"},
        ),
        # Shared cofounders do not collapse two founders.
        (
            {
                "candidate_name": "Ari Patel",
                "company_name": "Orbit Labs",
                "cofounders": ["Morgan Lee"],
            },
            {
                "candidate_name": "Rae Singh",
                "company_name": "Orbit Labs",
                "cofounders": ["Morgan Lee"],
            },
        ),
        # One person associated with different companies remains separate absent
        # explicit rename/domain continuity.
        (
            {"candidate_name": "Jordan Lee", "company_name": "Former Co"},
            {"candidate_name": "Jordan Lee", "company_name": "New Co"},
        ),
        # A coincidentally matching GitHub/social username is only a lead.
        (
            {
                "candidate_name": "Jamie Chen",
                "company_name": "North Labs",
                "usernames": ["builder"],
            },
            {
                "candidate_name": "Taylor Chen",
                "company_name": "South Labs",
                "usernames": ["builder"],
            },
        ),
        # Product and company names are not treated as aliases without evidence.
        (
            {"candidate_name": "Sam Rivera", "company_name": "Project Foo"},
            {"candidate_name": "Sam Rivera", "company_name": "Foo Labs"},
        ),
        # A domain migration alone does not join separate company-only histories.
        (
            {"company_name": "Atlas", "domains": ["atlas-old.example"]},
            {"company_name": "Atlas", "domains": ["atlas-new.example"]},
        ),
        # Acquisition/history language does not silently merge companies.
        (
            {"candidate_name": "Dana Wu", "company_name": "Acquired Co"},
            {"candidate_name": "Dana Wu", "company_name": "Parent Co"},
        ),
        # Indexed-profile ambiguity remains unresolved even when handles match.
        (
            {
                "candidate_name": "Chris Park",
                "company_name": "Alpha Systems",
                "usernames": ["chrisp"],
            },
            {
                "candidate_name": "Chris Park",
                "company_name": "Beta Systems",
                "usernames": ["chrisp"],
            },
        ),
    ],
)
def test_identity_stress_never_uses_weak_similarity_as_merge_proof(
    left_extra: dict[str, object], right_extra: dict[str, object]
) -> None:
    left = _observation(
        "https://identity-a.example/profile",
        provider="bing",
        stance="neutral",
        roles=["IDENTITY"],
        metadata_extra=left_extra,
    )
    right = _observation(
        "https://identity-b.example/profile",
        provider="brave",
        stance="neutral",
        roles=["IDENTITY"],
        metadata_extra=right_extra,
    )
    assert len(cluster_observations([left, right])) == 2


@pytest.mark.asyncio
async def test_analyst_epistemic_states_and_absence_language() -> None:
    provider = LocalBoundaryProvider()
    hypothesized = await provider.answer(
        "Is Acme in F26?",
        {
            "candidates": [
                {
                    "display_name": "Acme",
                    "confidence_pct": 61,
                    "state": "suspected",
                    "epistemic_state": "hypothesized",
                    "reason": "Only one independent origin.",
                    "sources": [],
                }
            ]
        },
    )
    assert hypothesized.epistemic_state is EpistemicState.HYPOTHESIZED
    assert "not confirmed" in hypothesized.answer
    known = await provider.answer(
        "Is Acme in F26?",
        {
            "candidates": [
                {
                    "display_name": "Acme",
                    "confidence_pct": 91,
                    "state": "confirmed",
                    "epistemic_state": "known",
                    "sources": [],
                }
            ]
        },
    )
    assert known.epistemic_state is EpistemicState.KNOWN
    assert "officially confirmed" in known.answer
    contradicted = await provider.answer(
        "Is Acme in F26?",
        {
            "candidates": [
                {
                    "display_name": "Acme",
                    "confidence_pct": 24,
                    "state": "rejected",
                    "epistemic_state": "contradicted",
                    "sources": [],
                }
            ]
        },
    )
    assert contradicted.epistemic_state is EpistemicState.CONTRADICTED
    assert "conflicts" in contradicted.answer
    unknown = await provider.answer("Is Acme in F26?", {})
    assert unknown.epistemic_state is EpistemicState.UNKNOWN
    assert "no evidence establishing" in unknown.answer
    unavailable = await provider.answer(
        "Is Acme in F26?", {"important_sources_unavailable": True}
    )
    assert unavailable.epistemic_state is EpistemicState.UNAVAILABLE
    assert "not successfully checked" in unavailable.answer


class UngroundedAnalystProvider:
    name = "unsafe-local-fixture"

    async def answer(self, _question: str, _context: dict) -> AnalystResult:
        return AnalystResult(
            answer="Acme is in YC F26 according to https://invented.example/claim.",
            model=self.name,
            evidence_sufficient=True,
        )


@pytest.mark.asyncio
async def test_unsupported_local_model_claim_is_discarded() -> None:
    agent = AnalystAgent(
        Settings(analyst_enabled=True, analyst_provider="local"),
        provider=UngroundedAnalystProvider(),
    )
    result = await agent.answer(
        "Is Acme in F26?",
        {
            "candidates": [
                {
                    "display_name": "Acme",
                    "confidence_pct": 55,
                    "state": "suspected",
                    "epistemic_state": "hypothesized",
                    "sources": [{"label": "Evidence", "url": "https://real.example/acme"}],
                }
            ],
            "citations": [{"label": "Evidence", "url": "https://real.example/acme"}],
        },
    )
    assert result.model == "local-boundary"
    assert result.epistemic_state is EpistemicState.HYPOTHESIZED
    assert "not confirmed" in result.answer
    assert any("discarded" in item for item in result.caveats)


class RoutingProvider:
    def __init__(self, domain: str, operation_status: SourceOperationStatus) -> None:
        self.domain = domain
        self.operation_status = operation_status
        self.queries: list[str] = []

    def set_request_budget(self, _tracker: object) -> None:
        return None

    async def search(self, query: str, _max_results: int) -> SearchResponse:
        self.queries.append(query)
        status = (
            ResearchSourceStatus.BLOCKED
            if self.operation_status is SourceOperationStatus.CIRCUIT_OPEN
            else ResearchSourceStatus.AVAILABLE
        )
        return SearchResponse(
            query=query,
            status=status,
            results=[],
            retrieved_at=NOW.isoformat(),
            operation_status=self.operation_status,
        )


@pytest.mark.asyncio
async def test_indexed_social_queries_route_through_healthy_search_providers(
    tmp_path: Path,
) -> None:
    ddg = RoutingProvider("duckduckgo", SourceOperationStatus.CIRCUIT_OPEN)
    bing = RoutingProvider("bing", SourceOperationStatus.EMPTY)
    brave = RoutingProvider("brave", SourceOperationStatus.EMPTY)
    service = BatchDiscoveryService(
        BatchDiscoveryRepository(Database(str(tmp_path / "routing.db"))),
        [ddg, bing, brave],
    )
    run = await service.run(
        BatchDiscoveryRequest(program_id="yc_current", budget=_budget()),
        observed_at=NOW,
    )
    assert any("site:linkedin.com" in query for query in ddg.queries)
    assert any("site:linkedin.com" in query for query in bing.queries)
    assert any("site:linkedin.com" in query for query in brave.queries)
    indexed = [item for item in run.source_outcomes if item.source_family == "indexed_social"]
    assert {item.provider_id for item in indexed} == {"duckduckgo", "bing", "brave"}
    ddg_outcome = next(item for item in indexed if item.provider_id == "duckduckgo")
    assert ddg_outcome.status is SourceOperationStatus.CIRCUIT_OPEN
    assert all(
        item.status is SourceOperationStatus.EMPTY
        for item in indexed
        if item.provider_id in {"bing", "brave"}
    )


class StaticDiscoveryService:
    def __init__(self, run: BatchDiscoveryRun) -> None:
        self.run_result = run

    async def run(self, *_args, **_kwargs) -> BatchDiscoveryRun:
        return self.run_result

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_change_intelligence_ignores_tiny_recomputation_but_keeps_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(str(tmp_path / "changes.db"))
    repository = BatchDiscoveryRepository(database)
    program = get_program_registry().get("yc_current")
    assert program is not None
    observation = _observation(
        "https://news.example/material",
        provider="bing",
        stance="supports",
        roles=["DISCOVERY", "VERIFICATION"],
    )
    previous = build_hypotheses(
        mark_source_independence([observation]),
        run_id="before",
        program=program,
        revision_for=lambda _value: 1,
    )[0]
    repository.save_hypothesis(previous)

    def cycle_for(candidate):
        run = BatchDiscoveryRun(
            run_id="material-run",
            program_id=program.id,
            batch_id=program.batch,
            budget=_budget(),
            status="completed",
            hypotheses=[candidate],
        )
        return IntelligenceCycleService(
            database=database,
            discovery_factory=lambda *_args, **_kwargs: StaticDiscoveryService(run),
        )

    tiny = previous.model_copy(
        update={
            "assessment": previous.assessment.model_copy(
                update={"confidence_pct": previous.assessment.confidence_pct + 1}
            )
        }
    )
    tiny_cycle = cycle_for(tiny)

    async def no_events() -> set[str]:
        return set()

    monkeypatch.setattr(tiny_cycle, "process_canonical_events", no_events)
    tiny_result = await tiny_cycle._run_program(program.id)
    assert tiny_result.changes == []

    confirmed = tiny.model_copy(update={"state": CandidateState.CONFIRMED})
    semantic_cycle = cycle_for(confirmed)
    monkeypatch.setattr(semantic_cycle, "process_canonical_events", no_events)
    semantic_result = await semantic_cycle._run_program(program.id)
    assert semantic_result.changes[0].change_types == ["lifecycle_state_changed"]


def test_minimal_feedback_vocabulary_is_typed_for_future_evaluation() -> None:
    required = {
        "confirmed",
        "dismissed",
        "duplicate",
        "identity_corrected",
        "priority_changed",
        "investigation_requested",
        "outreach",
        "meeting_scheduled",
        "diligence",
        "passed",
        "invested",
    }
    assert required <= {item.value for item in CRMActionType}


def test_digest_deduplicates_one_underlying_change_and_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import venture_intelligence.api.app as api_app

    change = {
        "hypothesis_id": "hypothesis-acme",
        "display_name": "Acme",
        "delta_pct": 8,
        "causal_observation_ids": ["origin-techcrunch"],
        "change_types": ["funding_event"],
    }
    candidate = {
        "display_name": "Acme",
        "confidence_pct": 70,
        "state": "suspected",
        "sources": [
            {"url": "https://techcrunch.com/acme"},
            {"url": "https://techcrunch.com/acme"},
        ],
    }
    monkeypatch.setattr(
        api_app,
        "_grounded_analyst_context",
        lambda _question: {
            "candidates": [candidate],
            "changes": [change, change],
        },
    )
    _subject, body, links = api_app._digest_content()
    assert body.count("Change: Acme") == 1
    assert links == ["https://techcrunch.com/acme"]
