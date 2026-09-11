"""Offline regressions for the explicitly gated M9 live validation boundary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from venture_intelligence.api.app import app
from venture_intelligence.batch_discovery.tools import LocalOsintToolRun
from venture_intelligence.config import settings
from venture_intelligence.crawler import CrawlResult
from venture_intelligence.operations.store import SourceQualityReportStore
from venture_intelligence.operations.validation import (
    _search_probe,
    bounded_live_budget,
    require_live_gate,
    run_live_validation,
)
from venture_intelligence.research.sources import SearchResponse, SearchResult
from venture_intelligence.shared.models import ResearchSourceStatus


@pytest.fixture(autouse=True)
def _isolate_local_osint_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline validation fixtures must never launch installed network tools."""
    import venture_intelligence.operations.validation as validation

    monkeypatch.setattr(settings, "product_hunt_api_enabled", False)
    monkeypatch.setattr(settings, "product_hunt_access_token", None)
    monkeypatch.setattr(settings, "github_token", None)
    monkeypatch.setattr(settings, "discord_api_enabled", False)
    monkeypatch.setattr(settings, "discord_bot_token", None)
    monkeypatch.setattr(settings, "firecrawl_api_url", None)
    monkeypatch.setattr(settings, "firecrawl_api_key", None)

    async def run(
        tool: str,
        *,
        username: str,
        allow_network: bool,
        known_positive_expected: bool,
    ) -> LocalOsintToolRun:
        assert allow_network is True
        assert known_positive_expected is True
        return LocalOsintToolRun(
            tool=tool.title(),
            installed=True,
            enabled=True,
            execution_status="LIVE_VALIDATED",
            input=username,
            sites_attempted=3,
            identity_effect="none; fixture remains non-authoritative",
        )

    monkeypatch.setattr(validation, "run_local_osint_tool", run)
    monkeypatch.setattr(validation, "BingSearchProvider", _FixtureProvider)
    monkeypatch.setattr(validation, "YahooSearchProvider", _FixtureProvider)
    monkeypatch.setattr(validation, "BraveSearchProvider", _FixtureProvider)


def _result(url: str, *, direct: bool = False) -> SearchResult:
    return SearchResult(
        url=url,
        title="Alice Chen and Acme Labs",
        snippet="Alice Chen says Acme Labs joined Y Combinator F26.",
        source_domain=url.split("/", 3)[2],
        published_at="2026-07-30T10:00:00+00:00",
        direct_public=direct,
        metadata={
            "candidate_kind": "person",
            "candidate_name": "Alice Chen",
            "company_name": "Acme Labs",
            "program_id": "yc_current",
            "batch_id": "F26",
            "batch_claim": True,
            "stance": "supports",
            "source_roles": ["DISCOVERY", "IDENTITY", "VERIFICATION"],
            "source_reliability": 0.9,
            "identity_confidence": 0.94,
            "directness": 0.92,
            "temporal_relevance": 0.96,
            "canonical_origin_url": url,
            "domains": ["acme.example"],
        },
        provenance={"fixture": "m9-offline-live-boundary"},
    )


class _FixtureProvider:
    domain = "duckduckgo.com"

    def __init__(self, *_args, **_kwargs) -> None:
        return None

    def set_request_budget(self, _tracker) -> None:
        return None

    async def search(self, query: str, _max_results: int) -> SearchResponse:
        if "alice chen" in query.casefold() or "acme labs" in query.casefold():
            results = [_result("https://acme.example/announcement", direct=True)]
        else:
            results = [_result("https://news.example/acme-yc")]
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE,
            results=results,
            retrieved_at="2026-08-26T00:00:00+00:00",
            attempts=1,
        )

    async def close(self) -> None:
        return None


class _SocialFixtureProvider(_FixtureProvider):
    domain = "public-program-osint"

    async def search(self, query: str, _max_results: int) -> SearchResponse:
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE,
            results=[_result("https://linkedin.com/posts/alice-acme-yc")],
            retrieved_at="2026-08-26T00:00:00+00:00",
            attempts=1,
        )


class _ArxivFixtureProvider(_FixtureProvider):
    domain = "arxiv.org"

    async def search(self, query: str, _max_results: int) -> SearchResponse:
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE,
            results=[SearchResult(url="https://arxiv.org/abs/2601.00001", title="Paper")],
            retrieved_at="2026-08-26T00:00:00+00:00",
        )


class _WaybackFixtureProvider(_FixtureProvider):
    domain = "web.archive.org"

    async def search(self, query: str, _max_results: int) -> SearchResponse:
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE,
            results=[
                SearchResult(
                    url="https://web.archive.org/web/20260101000000/https://example.com",
                    title="Archive",
                )
            ],
            retrieved_at="2026-08-26T00:00:00+00:00",
        )


class _FixtureCrawler:
    def __init__(self, *_args, **_kwargs) -> None:
        self.request_outcomes: list[dict] = []

    async def fetch_text_budgeted(self, url: str, *_args, **_kwargs) -> CrawlResult:
        if "api.github.com" in url:
            text = json.dumps(
                {"items": [{"html_url": "https://github.com/acme-labs/product"}]}
            )
        else:
            text = "Alice Chen founded Acme Labs and joined Y Combinator F26."
        return CrawlResult(
            url=url,
            text=text,
            status_code=200,
            source_status=ResearchSourceStatus.AVAILABLE,
        )

    async def close(self) -> None:
        return None


def test_live_gate_and_hard_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VENTURE_INTELLIGENCE_LIVE_TESTS", raising=False)
    with pytest.raises(PermissionError):
        require_live_gate(allow_network=True)
    monkeypatch.setenv("VENTURE_INTELLIGENCE_LIVE_TESTS", "1")
    with pytest.raises(PermissionError):
        require_live_gate(allow_network=False)
    require_live_gate(allow_network=True)
    budget = bounded_live_budget(max_queries=999, max_requests=999)
    assert budget.max_queries == 8
    assert budget.max_total_requests == 24


@pytest.mark.asyncio
async def test_indexed_social_control_requires_actual_platform_result() -> None:
    unrelated = await _search_probe(
        "indexed_linkedin:test",
        _FixtureProvider(),
        "site:linkedin.com/in known person",
        role="discovery",
        known_positive_control=True,
        expected_hosts={"linkedin.com"},
    )
    matched = await _search_probe(
        "indexed_linkedin:test",
        _SocialFixtureProvider(),
        "site:linkedin.com/in known person",
        role="discovery",
        known_positive_control=True,
        expected_hosts={"linkedin.com"},
    )
    assert unrelated.operation_status.name == "PARSER_ERROR"
    assert unrelated.state.value == "drifted"
    assert matched.operation_status.name == "SUCCESS"
    assert matched.indexed_social_hosts == ["linkedin.com"]


@pytest.mark.asyncio
async def test_isolated_live_report_preserves_policy_and_truthfulness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import venture_intelligence.operations.validation as validation

    monkeypatch.setenv("VENTURE_INTELLIGENCE_LIVE_TESTS", "1")
    monkeypatch.setattr(validation, "StealthCrawler", _FixtureCrawler)
    monkeypatch.setattr(validation, "DuckDuckGoSearchProvider", _FixtureProvider)
    monkeypatch.setattr(
        validation,
        "PublicProgramAnnouncementProvider",
        _SocialFixtureProvider,
    )
    monkeypatch.setattr(validation, "ArxivSearchProvider", _ArxivFixtureProvider)
    monkeypatch.setattr(validation, "WaybackSearchProvider", _WaybackFixtureProvider)
    report_path = tmp_path / "latest.json"

    report = await run_live_validation(
        program_id="yc_current",
        report_path=report_path,
        allow_network=True,
        max_queries=8,
        max_requests=24,
    )

    assert report.live_network_executed is True
    assert report.temporary_store_isolated is True
    assert report.operational_store_mutated is False
    assert report.discovery_quality.unknown_hypotheses >= 1
    assert report.discovery_quality.canonical_promotions == 0
    assert report.discovery_quality.precision_at_k is None
    assert report.discovery_quality.recall is None
    assert any(
        "linkedin.com" in item.indexed_social_hosts
        for item in report.source_diagnostics
    )
    assert all(
        item.direct_restricted_social_requests == 0
        for item in report.source_diagnostics
    )
    assert {item.platform for item in report.social_platform_diagnostics} == {
        "linkedin",
        "x",
        "instagram",
        "threads",
    }
    linkedin = next(
        item for item in report.social_platform_diagnostics if item.platform == "linkedin"
    )
    assert linkedin.observations >= 1
    assert linkedin.direct_requests == 0
    assert {item.tool for item in report.dedicated_osint_tools} == {"Maigret", "Sherlock"}
    assert all(item.identity_effect.startswith("none") for item in report.dedicated_osint_tools)
    inventory_ids = {item.connector for item in report.connector_inventory}
    assert {
        "directory",
        "github",
        "wayback",
        "firecrawl",
        "maigret",
        "sherlock",
        "indexed_linkedin",
        "indexed_x",
        "indexed_instagram",
        "indexed_threads",
    } <= inventory_ids
    assert all(
        item.official_api_implementation_status.value
        in {
            "OFFICIAL_API_LIVE_VALIDATED",
            "OFFICIAL_API_IMPLEMENTED_NOT_CONFIGURED",
            "OFFICIAL_API_BOUNDARY_ONLY",
            "OFFICIAL_API_NOT_SUPPORTED",
        }
        for item in report.connector_inventory
    )
    inventory = {item.connector: item for item in report.connector_inventory}
    assert inventory["product_hunt"].access_mode.value == "AUTHORIZED_OFFICIAL_API"
    assert inventory["product_hunt"].credentials_required is True
    assert inventory["discord"].access_mode.value == "PUBLIC_EMBED"
    assert inventory["discord_authorized_api"].access_mode.value == (
        "AUTHORIZED_OFFICIAL_API"
    )
    assert inventory["discord_authorized_api"].implementation_state.value == (
        "BOUNDARY_ONLY"
    )
    assert inventory["discord_authorized_api"].credentials_required is True
    assert inventory["triangulation"].source_coverage_behavior == (
        "ORCHESTRATION_ONLY_EXCLUDED_FROM_COVERAGE"
    )
    assert inventory["indexed_linkedin"].source_coverage_behavior == (
        "ATTRIBUTED_TO_SEARCH_PROVIDER_NOT_DOUBLE_COUNTED"
    )
    assert sum(
        item.requests
        for item in report.source_diagnostics
        if item.validation_scope == "normal_live_discovery"
    ) <= 24
    assert sum(
        item.requests
        for item in report.source_diagnostics
        if item.validation_scope == "known_positive_control"
    ) <= 24
    techcrunch_feed = next(
        item
        for item in report.source_diagnostics
        if item.source_id == "techcrunch:feed"
    )
    assert techcrunch_feed.requests == 0
    assert techcrunch_feed.operation_status.name in {"EMPTY", "UNAVAILABLE"}
    assert SourceQualityReportStore(report_path).load() == report
    report.source_diagnostics[0].source_id = "duckduckgo:known-positive"
    report.source_diagnostics[0].known_positive_control = True
    SourceQualityReportStore(report_path).save(report)
    monkeypatch.setattr(settings, "source_quality_report_path", str(report_path))
    with TestClient(app) as client:
        source_policy = client.get("/api/v1/sources")
    duckduckgo_runtime = next(
        item
        for item in source_policy.json()["runtime"]
        if item["source_id"] == "duckduckgo"
    )
    assert duckduckgo_runtime["health"] == report.source_diagnostics[0].state.value


def test_source_quality_api_is_read_only_and_typed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / "missing.json"
    monkeypatch.setattr(settings, "source_quality_report_path", str(report_path))
    with TestClient(app) as client:
        contract = client.get("/api/v1/source-quality/contract")
        response = client.get("/api/v1/source-quality")
    assert contract.status_code == 200
    assert contract.json()["execution_boundary"] == "local_cli_only"
    assert contract.json()["direct_social_access_enabled"] is False
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert not report_path.exists()


def test_source_quality_api_reports_corrupt_state_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / "corrupt.json"
    report_path.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(settings, "source_quality_report_path", str(report_path))
    with TestClient(app) as client:
        response = client.get("/api/v1/source-quality")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert "invalid" in response.json()["message"].casefold()


@pytest.mark.asyncio
async def test_ground_truth_enables_quality_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import venture_intelligence.operations.validation as validation

    monkeypatch.setenv("VENTURE_INTELLIGENCE_LIVE_TESTS", "1")
    monkeypatch.setattr(validation, "StealthCrawler", _FixtureCrawler)
    monkeypatch.setattr(validation, "DuckDuckGoSearchProvider", _FixtureProvider)
    monkeypatch.setattr(
        validation,
        "PublicProgramAnnouncementProvider",
        _SocialFixtureProvider,
    )
    monkeypatch.setattr(validation, "ArxivSearchProvider", _ArxivFixtureProvider)
    monkeypatch.setattr(validation, "WaybackSearchProvider", _WaybackFixtureProvider)
    truth = tmp_path / "truth.json"
    truth.write_text(
        json.dumps({"Acme Labs": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    report = await run_live_validation(
        program_id="yc_current",
        report_path=tmp_path / "report.json",
        allow_network=True,
        ground_truth_path=truth,
    )
    assert report.discovery_quality.ground_truth_available is True
    assert report.discovery_quality.precision_at_k is not None
    assert report.discovery_quality.recall is not None
