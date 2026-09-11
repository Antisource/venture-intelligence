"""M8 discovery-first acceptance and integrity regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from venture_intelligence.batch_discovery.engine import (
    discovery_queries,
    mark_source_independence,
    observation_from_result,
)
from venture_intelligence.batch_discovery.evaluation import evaluate_discovery
from venture_intelligence.batch_discovery.models import (
    BatchDiscoveryRequest,
    CandidateState,
    ManualBatchObservationRequest,
)
from venture_intelligence.batch_discovery.repository import BatchDiscoveryRepository
from venture_intelligence.batch_discovery.service import BatchDiscoveryService
from venture_intelligence.batch_discovery.tools import (
    TOOL_BENCHMARKS,
    ManualUsernameExportAdapter,
    local_osint_tool_diagnostics,
)
from venture_intelligence.operations.cycle import IntelligenceCycleService
from venture_intelligence.pipeline.entity_resolution import EntityResolver
from venture_intelligence.programs import get_program_registry
from venture_intelligence.research.sources import SearchResponse, SearchResult
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    CanonicalEvent,
    Company,
    EntityType,
    EventType,
    ResearchBudget,
    ResearchSourceStatus,
    Source,
)
from venture_intelligence.storage.db import Database

UTC = timezone.utc
T0 = datetime(2026, 8, 1, 9, tzinfo=UTC)


def _result(
    url: str,
    *,
    person: str,
    company: str,
    claim: str,
    roles: list[str],
    stance: str = "supports",
    direct_public: bool = False,
    origin: str | None = None,
    source_reliability: float = 0.92,
) -> SearchResult:
    domain = url.split("/", 3)[2]
    return SearchResult(
        url=url,
        title=f"{person} and {company}",
        snippet=claim,
        source_domain=domain,
        published_at="2026-07-30T10:00:00+00:00",
        direct_public=direct_public,
        metadata={
            "candidate_kind": "person",
            "candidate_name": person,
            "company_name": company,
            "program_id": "yc_current",
            "batch_id": "F26",
            "batch_claim": True,
            "stance": stance,
            "source_roles": roles,
            "source_reliability": source_reliability,
            "identity_confidence": 0.94,
            "directness": 0.94,
            "temporal_relevance": 0.96,
            "canonical_origin_url": origin or url,
            "domains": ["acme.example"] if company == "Acme Labs" else [],
            "background_claims": ["Built public developer infrastructure"],
        },
        provenance={"fixture": "m8-historical-public-signal"},
    )


class HistoricalFixtureProvider:
    """Offline provider: candidates are discovered from results, never input identities."""

    domain = "fixture-public-index"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def set_request_budget(self, _tracker: object) -> None:
        return None

    async def search(self, query: str, _max_results: int) -> SearchResponse:
        self.queries.append(query)
        lowered = query.casefold()
        results: list[SearchResult] = []
        if "alice chen" in lowered or "acme labs" in lowered:
            if "github.com" in lowered or "arxiv.org" in lowered:
                results = [
                    _result(
                        "https://github.com/alicechen/acme",
                        person="Alice Chen",
                        company="Acme Labs",
                        claim="Alice Chen maintains the public Acme Labs repository.",
                        roles=["IDENTITY", "ENRICHMENT"],
                        stance="neutral",
                        source_reliability=0.78,
                    )
                ]
            else:
                results = [
                    _result(
                        "https://acme.example/news/yc-s26",
                        person="Alice Chen",
                        company="Acme Labs",
                        claim="Alice Chen says Acme Labs joined Y Combinator F26.",
                        roles=["IDENTITY", "VERIFICATION"],
                        direct_public=True,
                    ),
                    _result(
                        "https://founderletter.example/issues/acme-s26",
                        person="Alice Chen",
                        company="Acme Labs",
                        claim="Independent newsletter reports Acme Labs joined YC F26.",
                        roles=["DISCOVERY", "VERIFICATION"],
                        direct_public=True,
                        source_reliability=0.88,
                    ),
                ]
        elif "mallory stone" in lowered or "false signal" in lowered:
            results = [
                _result(
                    "https://false.example/correction",
                    person="Mallory Stone",
                    company="False Signal",
                    claim="Correction: False Signal did not join Y Combinator F26.",
                    roles=["IDENTITY", "VERIFICATION"],
                    stance="contradicts",
                    direct_public=True,
                ),
                _result(
                    "https://program.example/other-batch",
                    person="Mallory Stone",
                    company="False Signal",
                    claim="False Signal belongs to another accelerator batch.",
                    roles=["VERIFICATION"],
                    stance="other_batch",
                    direct_public=True,
                ),
            ]
        elif "site:" not in lowered or "linkedin.com" in lowered:
            # The first indexed result is the unknown-candidate discovery signal.
            # The second is a derivative copy of the same original announcement.
            results = [
                _result(
                    "https://linkedin.com/posts/alice-joins-acme",
                    person="Alice Chen",
                    company="Acme Labs",
                    claim="Alice Chen: Acme Labs is joining Y Combinator F26.",
                    roles=["DISCOVERY"],
                    origin="https://linkedin.com/posts/alice-joins-acme",
                    source_reliability=0.58,
                ),
                _result(
                    "https://searchmirror.example/alice-joins-acme",
                    person="Alice Chen",
                    company="Acme Labs",
                    claim="Indexed copy: Acme Labs is joining Y Combinator F26.",
                    roles=["DISCOVERY"],
                    origin="https://linkedin.com/posts/alice-joins-acme",
                    source_reliability=0.54,
                ),
                _result(
                    "https://x.com/mallory/status/1",
                    person="Mallory Stone",
                    company="False Signal",
                    claim="Maybe False Signal is joining YC F26.",
                    roles=["DISCOVERY"],
                    source_reliability=0.5,
                ),
                _result(
                    "https://www.ycombinator.com/companies/directory-only",
                    person="Directory Only",
                    company="Directory Seed Co",
                    claim="Directory Only at Directory Seed Co, Y Combinator F26.",
                    roles=["VERIFICATION"],
                    direct_public=True,
                ),
            ]
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE,
            results=results,
            retrieved_at=T0.isoformat(),
        )


class SpeedrunFixtureProvider:
    domain = "fixture-speedrun-index"

    def set_request_budget(self, _tracker: object) -> None:
        return None

    async def search(self, query: str, _max_results: int) -> SearchResponse:
        lowered = query.casefold()
        if "github.com" in lowered or "arxiv.org" in lowered:
            results = [
                SearchResult(
                    url="https://github.com/samrivera/pixel-harbor",
                    title="Sam Rivera builds Pixel Harbor",
                    snippet="Sam Rivera maintains the public Pixel Harbor repository.",
                    source_domain="github.com",
                    direct_public=True,
                    metadata={
                        "candidate_kind": "person",
                        "candidate_name": "Sam Rivera",
                        "company_name": "Pixel Harbor",
                        "program_id": "speedrun_current",
                        "batch_id": "SR007",
                        "stance": "neutral",
                        "source_roles": ["IDENTITY", "ENRICHMENT"],
                        "source_reliability": 0.8,
                        "identity_confidence": 0.94,
                        "directness": 0.9,
                        "temporal_relevance": 0.96,
                        "domains": ["pixelharbor.example"],
                    },
                )
            ]
        else:
            results = [
                SearchResult(
                    url="https://pixelharbor.example/updates/sr007",
                    title="Sam Rivera and Pixel Harbor",
                    snippet="Sam Rivera says Pixel Harbor joined a16z Speedrun SR007.",
                    source_domain="pixelharbor.example",
                    direct_public=True,
                    metadata={
                        "candidate_kind": "person",
                        "candidate_name": "Sam Rivera",
                        "company_name": "Pixel Harbor",
                        "program_id": "speedrun_current",
                        "batch_id": "SR007",
                        "batch_claim": True,
                        "stance": "supports",
                        "source_roles": ["IDENTITY", "VERIFICATION"],
                        "source_reliability": 0.93,
                        "identity_confidence": 0.95,
                        "directness": 0.95,
                        "temporal_relevance": 0.97,
                        "domains": ["pixelharbor.example"],
                    },
                ),
                SearchResult(
                    url="https://gamesbrief.example/pixel-harbor-speedrun",
                    title="Pixel Harbor joins Speedrun",
                    snippet="Independent report: Sam Rivera and Pixel Harbor joined SR007.",
                    source_domain="gamesbrief.example",
                    direct_public=True,
                    metadata={
                        "candidate_kind": "person",
                        "candidate_name": "Sam Rivera",
                        "company_name": "Pixel Harbor",
                        "program_id": "speedrun_current",
                        "batch_id": "SR007",
                        "batch_claim": True,
                        "stance": "supports",
                        "source_roles": ["DISCOVERY", "VERIFICATION"],
                        "source_reliability": 0.88,
                        "identity_confidence": 0.92,
                        "directness": 0.9,
                        "temporal_relevance": 0.96,
                        "domains": ["pixelharbor.example"],
                    },
                ),
            ]
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE,
            results=results,
            retrieved_at=T0.isoformat(),
        )


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


@pytest.mark.asyncio
async def test_unknown_candidate_discovery_to_promotion_and_historical_replay(
    tmp_path: Path,
) -> None:
    database = Database(str(tmp_path / "m8.db"))
    repository = BatchDiscoveryRepository(database)
    provider = HistoricalFixtureProvider()
    service = BatchDiscoveryService(repository, provider)

    run = await service.run(
        BatchDiscoveryRequest(program_id="yc_current", budget=_budget()),
        event_bus=EventBus(database),
        observed_at=T0,
    )

    alice = next(item for item in run.hypotheses if item.candidate_name == "Alice Chen")
    mallory = next(item for item in run.hypotheses if item.candidate_name == "Mallory Stone")
    assert alice.assessment.promotion_eligible is True
    assert alice.assessment.independent_supporting_sources >= 2
    assert alice.assessment.derivative_observations >= 1
    assert alice.profile.github_url == "https://github.com/alicechen/acme"
    assert alice.profile.background_claims == ["Built public developer infrastructure"]
    assert alice.promoted_person_source_id and alice.promoted_company_source_id
    assert mallory.assessment.promotion_eligible is False
    assert mallory.assessment.contradictory_observations >= 1
    assert mallory.state in {CandidateState.REJECTED, CandidateState.UNCERTAIN}
    assert all(item.display_name != "Directory Only" for item in run.hypotheses)
    assert any("joining" in query.casefold() for query in provider.queries)
    program = get_program_registry().get("yc_current")
    assert program is not None
    assert all("Alice Chen" not in query for query in discovery_queries(program))

    with database._connect() as connection:
        event_rows = connection.execute(
            "SELECT raw_payload FROM events ORDER BY event_id"
        ).fetchall()
    payloads = " ".join(row["raw_payload"] for row in event_rows)
    assert "Alice Chen" in payloads and "Acme Labs" in payloads
    assert "Mallory Stone" not in payloads and "False Signal" not in payloads

    affected = await IntelligenceCycleService(database=database).process_canonical_events()
    assert affected
    assert {item.canonical_name for item in database.all_companies()} == {"Acme Labs"}
    assert {item.canonical_name for item in database.all_persons()} == {"Alice Chen"}
    assert database.get_rankings()
    with database._connect() as connection:
        graph_labels = {
            row[0]
            for row in connection.execute("SELECT label FROM graph_nodes").fetchall()
        }
    assert {"Acme Labs", "Alice Chen"} <= graph_labels
    assert not {"twitter", "github", "keyframes"} & {
        value.casefold() for value in graph_labels
    }

    first = service.replay("yc_current", T0 + timedelta(hours=1))
    second = service.replay("yc_current", T0 + timedelta(hours=1))
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    replayed_alice = next(item for item in first.hypotheses if item.candidate_name == "Alice Chen")
    assert replayed_alice.assessment.promotion_eligible is True
    assert first.live_state_mutated is False

    evaluation = evaluate_discovery(
        run.hypotheses,
        ground_truth={"Acme Labs": T0 + timedelta(days=10)},
        observations=run.observations,
        program=program,
    )
    assert evaluation.precision_at_k == 1
    assert evaluation.recall == 1
    assert evaluation.mean_lead_time_days == 10
    assert "acme.example" in evaluation.source_ablation_promotion_loss
    assert evaluation.acceptance_confidence_brier_score is not None
    assert evaluation.acceptance_confidence_brier_score < 0.25


@pytest.mark.asyncio
async def test_query_budget_preserves_evidence_and_reports_partial_run(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "m8-partial.db"))
    service = BatchDiscoveryService(
        BatchDiscoveryRepository(database),
        HistoricalFixtureProvider(),
    )
    budget = _budget().model_copy(update={"max_queries": 1})

    run = await service.run(
        BatchDiscoveryRequest(program_id="yc_current", budget=budget),
        observed_at=T0,
    )

    assert run.status == "partial"
    assert run.stop_reason == "max_queries_reached"
    assert run.observations
    assert any(item.candidate_name == "Alice Chen" for item in run.hypotheses)


@pytest.mark.asyncio
async def test_m8_discovery_is_program_registry_driven_for_speedrun(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "m8-speedrun.db"))
    service = BatchDiscoveryService(
        BatchDiscoveryRepository(database),
        SpeedrunFixtureProvider(),
    )

    run = await service.run(
        BatchDiscoveryRequest(program_id="speedrun_current", budget=_budget()),
        event_bus=EventBus(database),
        observed_at=T0,
    )

    candidate = next(item for item in run.hypotheses if item.candidate_name == "Sam Rivera")
    assert run.batch_id == "SR007"
    assert candidate.program_id == "speedrun_current"
    assert candidate.assessment.promotion_eligible is True
    assert candidate.profile.github_url == "https://github.com/samrivera/pixel-harbor"
    assert all("Y Combinator" not in query for query in run.queries)
    with database._connect() as connection:
        metadata = " ".join(
            row["metadata"]
            for row in connection.execute("SELECT metadata FROM events").fetchall()
        )
    assert "speedrun_current" in metadata


def test_late_observation_does_not_leak_into_historical_replay(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "m8-replay.db"))
    service = BatchDiscoveryService(BatchDiscoveryRepository(database), [])
    service.ingest_manual(
        ManualBatchObservationRequest(
            program_id="yc_current",
            candidate_kind="person",
            candidate_name="Alice Chen",
            company_name="Acme Labs",
            claim="Acme Labs joined YC F26.",
            source_url="https://acme.example/announcement",
            stance="supports",
            source_roles=["IDENTITY", "VERIFICATION"],
        ),
        observed_at=T0,
    )
    service.ingest_manual(
        ManualBatchObservationRequest(
            program_id="yc_current",
            candidate_kind="person",
            candidate_name="Alice Chen",
            company_name="Acme Labs",
            claim="Later correction contradicts the claim.",
            source_url="https://news.example/correction",
            stance="contradicts",
            source_roles=["VERIFICATION"],
        ),
        observed_at=T0 + timedelta(days=2),
    )
    replay = service.replay("yc_current", T0 + timedelta(days=1))
    candidate = replay.hypotheses[0]
    assert candidate.assessment.contradictory_observations == 0
    assert replay.historical_semantics == "observed_at <= cutoff"


def test_taxonomy_strict_extraction_and_derivative_origins() -> None:
    program = get_program_registry().get("yc_current")
    assert program is not None
    social = _result(
        "https://x.com/alice/status/1",
        person="Alice Chen",
        company="Acme Labs",
        claim="Acme Labs is joining YC F26.",
        roles=["DISCOVERY"],
    )
    copied = social.model_copy(
        update={
            "url": "https://index.example/copy",
            "source_domain": "index.example",
            "metadata": {
                **social.metadata,
                "canonical_origin_url": "https://x.com/alice/status/1",
            },
        }
    )
    first = observation_from_result(
        result=social,
        query='"YC S26" founder',
        provider_id="fixture",
        run_id="run",
        program=program,
        observed_at=T0,
    )
    second = observation_from_result(
        result=copied,
        query='"YC S26" founder',
        provider_id="fixture",
        run_id="run",
        program=program,
        observed_at=T0,
    )
    assert first is not None and second is not None
    independent = mark_source_independence([first, second])
    assert sorted(item.independence.value for item in independent) == ["derivative", "independent"]

    keyword_only = SearchResult(
        url="https://example.com/css",
        title="keyframes twitter github",
        snippet="YC F26 joining founder",
        source_domain="example.com",
    )
    assert observation_from_result(
        result=keyword_only,
        query="keyframes twitter github",
        provider_id="fixture",
        run_id="run",
        program=program,
        observed_at=T0,
    ) is None


def test_osint_benchmark_and_manual_adapter_never_execute_network() -> None:
    decisions = {item.tool: item.decision.value for item in TOOL_BENCHMARKS}
    assert decisions == {
        "Maigret": "ADAPTER_ONLY",
        "Sherlock": "ADAPTER_ONLY",
    }
    result = ManualUsernameExportAdapter().parse(
        "sherlock",
        {"github": {"url": "https://github.com/alicechen", "status": "claimed"}},
        username="alicechen",
    )
    assert result[0].provenance["tool_network_execution"] is False
    assert result[0].metadata["source_roles"] == ["IDENTITY", "ENRICHMENT"]


def test_local_osint_availability_is_truthful_and_has_no_identity_effect() -> None:
    diagnostics = local_osint_tool_diagnostics()
    assert {item["tool"] for item in diagnostics} == {"Maigret", "Sherlock"}
    assert all(item["enabled"] is False for item in diagnostics)
    assert all(
        item["execution_status"] in {"NOT_INSTALLED", "AVAILABLE_NOT_EXECUTED"}
        for item in diagnostics
    )
    assert all(item["identity_effect"].startswith("none") for item in diagnostics)


def test_m8_migration_is_additive_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "migration.db"
    first = Database(str(path))
    with first._connect() as connection:
        connection.execute("DROP TABLE batch_discovery_runs")
        connection.execute("DROP TABLE batch_discovery_observations")
        connection.execute("DROP TABLE batch_candidate_hypotheses")
        connection.execute("DELETE FROM schema_migrations WHERE version='0005_batch_discovery'")
    Database(str(path))
    database = Database(str(path))
    with database._connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        applied = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version='0005_batch_discovery'"
        ).fetchone()[0]
    assert {
        "batch_discovery_runs",
        "batch_discovery_observations",
        "batch_candidate_hypotheses",
    } <= tables
    assert applied == 1


def test_m8_hypotheses_do_not_fuzzy_merge_promoted_companies(tmp_path: Path) -> None:
    database = Database(str(tmp_path / "resolver.db"))
    database.write_company(Company(company_id="existing", canonical_name="NeuralStack"))
    event = CanonicalEvent(
        event_type=EventType.COMPANY_LISTED,
        entity_type=EntityType.COMPANY,
        entity_id="batch-candidate__neuralstack-inc",
        source=Source.RESEARCH_AGENT,
        source_url="https://neuralstack.example/announcement",
        occurred_at=T0,
        observed_at=T0,
        ingested_at=T0,
        confidence=0.9,
        raw_payload={
            "name": "NeuralStack Inc",
            "supporting_source_count": 2,
            "domains": ["neuralstack.example"],
        },
        metadata={
            "candidate_hypothesis_id": "hypothesis-1",
            "program_id": "yc_current",
            "batch": "S26",
            "validation_status": "validated",
            "identity_validated": True,
            "identity_evidence": True,
            "supporting_source_count": 2,
                "supporting_source_domains": ["neuralstack.example", "news.example"],
                "identity_evidence_urls": [
                    "https://neuralstack.example/announcement",
                    "https://news.example/neuralstack",
                ],
                "evidence_tier": "corroborated_public_source",
                "assessment": {
                    "promotion_eligible": True,
                    "identity_corroborated": True,
                    "independent_supporting_sources": 2,
                },
            },
    )
    resolved = EntityResolver(database).resolve(event)
    assert resolved != "existing"
    assert len(database.all_companies()) == 2


def test_m8_api_contract_and_candidate_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from venture_intelligence.api.app import app
    from venture_intelligence.config import settings

    path = tmp_path / "api.db"
    monkeypatch.setattr(settings, "db_path", str(path))
    with TestClient(app) as client:
        contract = client.get("/api/v1/batch-discovery/contract")
        assert contract.status_code == 200
        payload = contract.json()
        assert payload["directory_role"] == "verification_and_fallback_seed_only"
        assert payload["restricted_social_mode"] == "permitted_search_index_discovery_only"
        assert payload["source_taxonomy"] == [
            "DISCOVERY",
            "IDENTITY",
            "VERIFICATION",
            "ENRICHMENT",
            "HISTORICAL",
        ]
        assert payload["benchmark_mode"] == "deterministic_offline_representative_fixtures"
        capabilities = {item["source_id"]: item for item in payload["source_capabilities"]}
        assert capabilities["public-search-index"]["seeds_unknown_discovery"] is True
        assert capabilities["official-program-directory"]["seeds_unknown_discovery"] is False
        assert all(
            item["direct_restricted_social_access"] is False
            for item in capabilities.values()
        )
        created = client.post(
            "/api/v1/batch-discovery/manual-observations",
            json={
                "program_id": "yc_current",
                "candidate_kind": "person",
                "candidate_name": "Alice Chen",
                "company_name": "Acme Labs",
                "claim": "Acme Labs joined YC F26.",
                "source_url": "https://acme.example/announcement",
                "stance": "supports",
                "source_roles": ["IDENTITY", "VERIFICATION"],
            },
        )
        assert created.status_code == 200
        listed = client.get(
            "/api/v1/batch-discovery",
            params={"program_id": "yc_current"},
        )
        assert listed.status_code == 200
        assert listed.json()["candidates"][0]["candidate_name"] == "Alice Chen"
        detail = client.get(
            f"/api/v1/batch-discovery/{created.json()['hypothesis_id']}"
        )
        assert detail.status_code == 200
        assert detail.json()["candidate"]["candidate_name"] == "Alice Chen"
        assert detail.json()["observations"][0]["claim"] == "Acme Labs joined YC F26."

    schema = app.openapi()
    response_schema = schema["paths"]["/api/v1/batch-discovery/runs"]["post"]["responses"]
    assert "200" in response_schema
