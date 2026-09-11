"""Final product-completion regressions across registry, routing, cycles, and delivery."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from venture_intelligence.agents.analyst import AnalystAgent, LocalBoundaryProvider
from venture_intelligence.api.app import app
from venture_intelligence.batch_discovery.models import BatchDiscoveryRun
from venture_intelligence.config import Settings, settings
from venture_intelligence.operations.cycle import (
    CycleAlreadyRunning,
    CycleStatus,
    IntelligenceCycleRecord,
    IntelligenceCycleService,
    OperationsStateStore,
)
from venture_intelligence.operations.readiness import system_readiness
from venture_intelligence.pipeline.email import EmailNotificationService
from venture_intelligence.programs import ProgramRegistry, get_program_registry
from venture_intelligence.research.agent import ResearchAgent
from venture_intelligence.research.provider_policy import (
    ARXIV_POLICY,
    DUCKDUCKGO_POLICY,
)
from venture_intelligence.research.query import QueryGenerator
from venture_intelligence.research.sources import SearchResponse, SearchResult
from venture_intelligence.shared.models import (
    ResearchBudget,
    ResearchQuery,
    ResearchQueryIntent,
    ResearchSourceStatus,
    ResearchTarget,
    ResearchTargetType,
)
from venture_intelligence.storage.db import Database


class _Provider:
    def __init__(self, domain: str, policy, results: list[SearchResult]) -> None:
        self.domain = domain
        self.policy = policy
        self.results = results
        self.calls: list[str] = []

    async def search(self, query: str, max_results: int) -> SearchResponse:
        self.calls.append(query)
        return SearchResponse(
            query=query,
            status=ResearchSourceStatus.AVAILABLE,
            results=self.results[:max_results],
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            attempts=1,
        )

    async def close(self) -> None:
        return None


class _OneQuery(QueryGenerator):
    def __init__(self, query: ResearchQuery) -> None:
        self.query = query

    def generate(self, target: ResearchTarget) -> list[ResearchQuery]:
        del target
        return [self.query]

    def follow_ups(self, target, evidence, next_depth):
        del target, evidence, next_depth
        return []


def _budget() -> ResearchBudget:
    return ResearchBudget(
        max_queries=1,
        max_depth=0,
        max_results_per_query=3,
        max_runtime_seconds=5,
        max_total_requests=6,
        max_requests_per_provider=3,
        max_retry_requests=1,
        max_requests_per_domain=3,
    )


def test_yc_has_one_canonical_identity_with_f26_current_and_s26_historical() -> None:
    registry = ProgramRegistry.load()
    yc = registry.get("yc_current")
    assert yc is not None
    assert yc.batch == yc.current_batch == "F26"
    assert yc.name == "Y Combinator"
    assert registry.batch("yc_current", "F26").name == "Fall 2026"
    assert registry.batch("yc_current", "S26").historical is True
    assert len([item for item in registry.active() if item.entity_id_prefix == "yc"]) == 1


def test_web_query_skips_arxiv_without_call_or_fake_evidence() -> None:
    web = _Provider(
        "duckduckgo.com",
        DUCKDUCKGO_POLICY,
        [
            SearchResult(
                url="https://example.com/acme",
                title="Acme Labs joined YC F26",
                snippet="Acme Labs announced its Fall 2026 accelerator participation.",
                source_domain="example.com",
            )
        ],
    )
    arxiv = _Provider(
        "arxiv.org",
        ARXIV_POLICY,
        [
            SearchResult(
                url="https://arxiv.org/abs/2601.1",
                title="Unrelated paper",
                source_domain="arxiv.org",
                discovery_method="public_api",
            )
        ],
    )
    query = ResearchQuery(
        query='site:linkedin.com "Acme Labs" "YC F26"',
        family="site",
        intent=ResearchQueryIntent.WEB_DISCOVERY,
    )
    run = asyncio.run(
        ResearchAgent(
            [web, arxiv],
            query_generator=_OneQuery(query),
            runtime_settings=Settings(research_cache_ttl_seconds=60),
        ).run(
            ResearchTarget(name="Acme Labs", type=ResearchTargetType.COMPANY),
            _budget(),
        )
    )
    assert len(web.calls) == 1
    assert arxiv.calls == []
    assert run.provider_skips[0].provider_id == "arxiv"
    assert run.provider_skips[0].request_count == 0
    assert run.relevant_results == 1
    assert all(item.source_domain != "arxiv.org" for item in run.evidence)


def test_publication_query_reaches_arxiv_but_unrelated_result_is_rejected() -> None:
    arxiv = _Provider(
        "arxiv.org",
        ARXIV_POLICY,
        [
            SearchResult(
                url="https://arxiv.org/abs/2601.1",
                title="Ocean circulation modelling",
                snippet="A study of marine currents.",
                source_domain="arxiv.org",
                discovery_method="public_api",
            )
        ],
    )
    query = ResearchQuery(
        query='"Acme Labs" machine learning',
        family="research",
        intent=ResearchQueryIntent.PUBLICATION,
    )
    run = asyncio.run(
        ResearchAgent(
            arxiv,
            query_generator=_OneQuery(query),
            runtime_settings=Settings(research_cache_ttl_seconds=60),
        ).run(
            ResearchTarget(name="Acme Labs", type=ResearchTargetType.COMPANY),
            _budget(),
        )
    )
    assert len(arxiv.calls) == 1
    assert run.retrieved_results == 1
    assert run.relevant_results == 0
    assert run.rejected_results[0].reason == "target_query_mismatch"
    assert run.confidence == 0
    assert run.confidence_label == "insufficient_evidence"
    assert run.stop_reason == "no_relevant_evidence"
    assert run.entities == []
    assert run.hypotheses == []
    assert run.evidence == []


class _EmptyDiscovery:
    def __init__(self, program_id: str, database: Database) -> None:
        self.program_id = program_id
        self.database = database

    async def run(self, request, event_bus=None):
        del event_bus
        program = get_program_registry().get(self.program_id)
        return BatchDiscoveryRun(
            program_id=self.program_id,
            batch_id=request.batch_id or program.batch,
            status="completed",
            stop_reason="completed",
            budget=_budget(),
            finished_at=datetime.now(timezone.utc),
        )

    async def close(self) -> None:
        return None


def test_cli_storage_error_is_structured_without_traceback_or_secrets(monkeypatch, capsys):
    from venture_intelligence import cli

    def fail(_args):
        raise OSError("sensitive-path-or-token")

    monkeypatch.setattr(cli, "_sync_command", fail)
    assert cli.main(["--json", "sources", "status"]) == 1
    captured = capsys.readouterr()
    assert '"BROKEN"' in captured.out
    assert "sensitive-path-or-token" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_cycle_rejects_unknown_program_before_claiming_success(tmp_path: Path) -> None:
    runtime = Settings(
        db_path=str(tmp_path / "cycle.db"),
        operations_state_path=str(tmp_path / "state.json"),
        operations_lock_path=str(tmp_path / "cycle.lock"),
    )
    service = IntelligenceCycleService(runtime_settings=runtime)
    with pytest.raises(ValueError, match="Unknown or inactive program"):
        asyncio.run(service.run_once(program_ids=["not-a-program"]))
    assert not Path(runtime.operations_lock_path).exists()
    assert service.store.load().cycles == []


def test_cycle_persists_twelve_hour_schedule_and_reuses_current_batch(tmp_path: Path) -> None:
    runtime = Settings(
        db_path=str(tmp_path / "cycle.db"),
        operations_state_path=str(tmp_path / "state.json"),
        operations_lock_path=str(tmp_path / "cycle.lock"),
        scheduler_interval_hours=12,
    )
    database = Database(runtime.db_path)

    def factory(program_id: str, **kwargs):
        return _EmptyDiscovery(program_id, kwargs["database"])

    service = IntelligenceCycleService(
        database=database,
        runtime_settings=runtime,
        discovery_factory=factory,
    )
    cycle = asyncio.run(
        service.run_once(program_ids=["yc_current"], trigger="test")
    )
    persisted = OperationsStateStore(runtime.operations_state_path).load()
    assert cycle.status.value == "completed"
    assert cycle.program_results[0].batch_id == "F26"
    assert cycle.next_run_at - cycle.started_at == timedelta(hours=12)
    assert persisted.cycles[0].cycle_id == cycle.cycle_id
    assert persisted.scheduler.next_run_at == cycle.next_run_at
    assert not Path(runtime.operations_lock_path).exists()


class _FailingDiscovery(_EmptyDiscovery):
    async def run(self, request, event_bus=None):
        del request, event_bus
        raise RuntimeError("bounded provider unavailable")


def test_scheduler_restart_overlap_failure_and_enable_disable(tmp_path: Path) -> None:
    runtime = Settings(
        db_path=str(tmp_path / "scheduler.db"),
        operations_state_path=str(tmp_path / "scheduler.json"),
        operations_lock_path=str(tmp_path / "scheduler.lock"),
        scheduler_interval_hours=12,
    )
    database = Database(runtime.db_path)

    def empty_factory(program_id: str, **kwargs):
        return _EmptyDiscovery(program_id, kwargs["database"])

    service = IntelligenceCycleService(
        database=database,
        runtime_settings=runtime,
        discovery_factory=empty_factory,
    )
    service.set_scheduler_enabled(False)
    restarted = IntelligenceCycleService(
        database=database,
        runtime_settings=runtime,
        discovery_factory=empty_factory,
    )
    assert restarted.store.scheduler().enabled is False
    restarted.set_scheduler_enabled(True)
    assert restarted.store.scheduler().enabled is True
    asyncio.run(restarted.run_scheduler(once=True))
    scheduler = restarted.store.scheduler()
    assert scheduler.running is False
    assert scheduler.last_cycle_id is not None
    persisted_cycle = next(
        item
        for item in restarted.store.load().cycles
        if item.cycle_id == scheduler.last_cycle_id
    )
    assert scheduler.next_run_at == persisted_cycle.started_at + timedelta(hours=12)
    assert scheduler.last_status is CycleStatus.COMPLETED
    assert scheduler.last_successful_run_at == persisted_cycle.finished_at

    Path(runtime.operations_lock_path).write_text("{}", encoding="utf-8")
    with pytest.raises(CycleAlreadyRunning) as overlap:
        asyncio.run(restarted.run_once(trigger="overlap-test"))
    Path(runtime.operations_lock_path).unlink()
    assert overlap.value.record is not None
    assert overlap.value.record.status is CycleStatus.SKIPPED_OVERLAP
    assert restarted.store.load().cycles[0].status is CycleStatus.SKIPPED_OVERLAP
    assert restarted.store.scheduler().next_run_at == persisted_cycle.next_run_at

    def failing_factory(program_id: str, **kwargs):
        return _FailingDiscovery(program_id, kwargs["database"])

    failed = asyncio.run(
        IntelligenceCycleService(
            database=database,
            runtime_settings=runtime,
            discovery_factory=failing_factory,
        ).run_once(program_ids=["yc_current"], trigger="source-failure-test")
    )
    assert failed.status.value == "partial"
    assert failed.program_results[0].stop_reason == "provider_or_pipeline_failure"
    assert failed.program_results[0].errors == ["bounded provider unavailable"]
    assert not Path(runtime.operations_lock_path).exists()
    scheduler = restarted.store.scheduler()
    assert scheduler.last_status is CycleStatus.PARTIAL
    assert scheduler.last_failed_run_at == failed.finished_at
    assert scheduler.last_failure_reason == "bounded provider unavailable"


def test_cycle_lock_never_reclaims_a_live_process(tmp_path: Path) -> None:
    runtime = Settings(
        _env_file=None,
        operations_state_path=str(tmp_path / "state.json"),
        operations_lock_path=str(tmp_path / "cycle.lock"),
        operations_lock_stale_seconds=300,
    )
    lock = Path(runtime.operations_lock_path)
    lock.write_text(json.dumps({"pid": os.getpid(), "token": "live"}), encoding="utf-8")
    old = datetime.now(timezone.utc).timestamp() - 3600
    os.utime(lock, (old, old))
    service = IntelligenceCycleService(runtime_settings=runtime)
    with pytest.raises(CycleAlreadyRunning):
        service._acquire_lock()
    assert lock.exists()


def test_cycle_lock_recovers_dead_and_old_invalid_owners(tmp_path: Path) -> None:
    runtime = Settings(
        _env_file=None,
        operations_state_path=str(tmp_path / "state.json"),
        operations_lock_path=str(tmp_path / "cycle.lock"),
        operations_lock_stale_seconds=300,
    )
    lock = Path(runtime.operations_lock_path)
    service = IntelligenceCycleService(runtime_settings=runtime)

    lock.write_text(json.dumps({"pid": 99_999_999, "token": "dead"}), encoding="utf-8")
    token, reason = service._acquire_lock()
    assert reason == "recovered lock owned by non-running pid 99999999"
    service._release_lock(token)

    lock.write_text("{}", encoding="utf-8")
    with pytest.raises(CycleAlreadyRunning):
        service._acquire_lock()
    old = datetime.now(timezone.utc).timestamp() - 3600
    os.utime(lock, (old, old))
    token, reason = service._acquire_lock()
    assert reason == "recovered invalid lock older than 300 seconds"
    service._release_lock(token)


def test_cycle_cli_exit_codes_are_scheduler_safe(monkeypatch, capsys) -> None:
    import venture_intelligence.cli as cli

    statuses = iter(
        [
            CycleStatus.COMPLETED,
            CycleStatus.PARTIAL,
            CycleStatus.FAILED,
        ]
    )

    async def fake_async_command(_args):
        return IntelligenceCycleRecord(status=next(statuses))

    monkeypatch.setattr(cli, "_async_command", fake_async_command)
    assert cli.main(["--json", "cycle", "run"]) == 0
    assert cli.main(["--json", "cycle", "run"]) == 1
    assert cli.main(["--json", "cycle", "run"]) == 1

    async def overlap(_args):
        record = IntelligenceCycleRecord(status=CycleStatus.SKIPPED_OVERLAP)
        raise CycleAlreadyRunning("already running", record)

    monkeypatch.setattr(cli, "_async_command", overlap)
    assert cli.main(["--json", "cycle", "run"]) == 3
    assert '"skipped_overlap"' in capsys.readouterr().out


def test_email_preview_and_deterministic_analyst_are_truthful(tmp_path: Path) -> None:
    email = EmailNotificationService(tmp_path / "email.json")
    preview = email.preview("Partner digest", "No candidates.")
    assert preview["status"] == "preview"
    assert preview["delivery_configured"] is False
    assert preview["notice"] == "DELIVERY NOT CONFIGURED — PREVIEW AVAILABLE"
    assert email.load()["deliveries"] == []

    result = asyncio.run(
        LocalBoundaryProvider().answer(
            "Why Acme?",
            {
                "candidates": [
                    {
                        "display_name": "Acme Labs",
                        "confidence_pct": 79,
                        "state": "corroborated",
                        "reason": "Two independent sources.",
                        "sources": [
                            {
                                "label": "example.com",
                                "url": "https://example.com/acme",
                            }
                        ],
                    }
                ]
            },
        )
    )
    assert result.evidence_sufficient is True
    assert "79%" in result.answer
    assert result.citations[0]["url"] == "https://example.com/acme"


def test_local_analyst_health_does_not_probe_or_claim_ollama() -> None:
    runtime = Settings(analyst_enabled=True, analyst_provider="local")
    agent = AnalystAgent(runtime)

    health = asyncio.run(agent.health())
    result = asyncio.run(agent.answer("What is known?", {}))

    assert health["status"] == "HEALTHY"
    assert health["provider"] == "local-boundary"
    assert health["model"] is None
    assert result.model == "local-boundary"
    assert result.evidence_sufficient is False
    assert "No conversational model knowledge" in result.caveats[0]


def test_readiness_api_exposes_f26_without_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "api.db"))
    monkeypatch.setattr(settings, "operations_state_path", str(tmp_path / "state.json"))
    monkeypatch.setattr(settings, "email_config_path", str(tmp_path / "email.json"))
    with TestClient(app) as client:
        response = client.get("/api/v1/system/readiness")
        programs = client.get("/api/v1/programs")
        search = client.get("/api/v1/search", params={"q": "YC F26"})
    assert response.status_code == 200
    assert response.json()["current_batch"] == "F26"
    assert programs.json()["programs"][0]["batch"] == "F26"
    assert search.status_code == 200
    assert search.json()[0]["id"] == "yc_current"
    assert search.json()[0]["context"].startswith("F26")
    serialized = response.text.casefold()
    assert "smtp_password" not in serialized
    assert "api_key" not in serialized


def test_readiness_classifies_optional_integrations_without_secrets(tmp_path: Path) -> None:
    runtime_settings = Settings(
        _env_file=None,
        db_path=str(tmp_path / "readiness.db"),
        operations_state_path=str(tmp_path / "operations.json"),
        source_quality_report_path=str(tmp_path / "source-quality.json"),
        email_config_path=str(tmp_path / "email.json"),
        product_hunt_api_enabled=True,
        product_hunt_access_token="product-hunt-secret",
        github_token="github-secret",
        discord_api_enabled=False,
        firecrawl_api_url=None,
        firecrawl_api_key=None,
    )

    readiness = system_readiness(runtime_settings)
    components = {item.component: item.status.value for item in readiness.components}
    serialized = readiness.model_dump_json()

    assert components["Product Hunt"] == "READY"
    assert components["GitHub Authentication"] == "READY"
    assert components["Discord Authorized API"] == "OPTIONAL_NOT_CONFIGURED"
    assert components["Firecrawl"] == "OPTIONAL_NOT_CONFIGURED"
    assert "product-hunt-secret" not in serialized
    assert "github-secret" not in serialized


def test_email_runtime_status_separates_transport_destination_and_enablement(
    tmp_path: Path,
) -> None:
    service = EmailNotificationService(
        tmp_path / "email.json",
        smtp_host="smtp.example",
        smtp_username="operator",
        smtp_password="secret",
        smtp_from="alerts@example.com",
    )
    service.configure(None, preferences={"enabled": True})

    status = service.status()

    assert status == {
        "transport_status": "CONFIGURED",
        "destination_status": "NOT_CONFIGURED",
        "sending_status": "DISABLED",
        "last_outcome": None,
    }
    assert "secret" not in json.dumps(status)


def test_readiness_uses_same_email_runtime_semantics(tmp_path: Path) -> None:
    runtime_settings = Settings(
        _env_file=None,
        db_path=str(tmp_path / "readiness.db"),
        operations_state_path=str(tmp_path / "operations.json"),
        source_quality_report_path=str(tmp_path / "source-quality.json"),
        email_config_path=str(tmp_path / "email.json"),
        email_smtp_host="smtp.example",
        email_smtp_username="operator",
        email_smtp_password="secret",
        email_smtp_from="alerts@example.com",
    )
    EmailNotificationService(runtime_settings.email_config_path).configure(
        None, preferences={"enabled": True}
    )

    readiness = system_readiness(runtime_settings)
    email_component = next(
        item for item in readiness.components if item.component == "Email Delivery"
    )

    assert readiness.email.transport_status == "CONFIGURED"
    assert readiness.email.destination_status == "NOT_CONFIGURED"
    assert readiness.email.sending_status == "DISABLED"
    assert "SMTP transport is configured" in email_component.summary
