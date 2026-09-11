"""Bounded discovery → verification → enrichment orchestration for M8."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Sequence
from urllib.parse import urlsplit

from venture_intelligence.batch_discovery.engine import (
    build_hypotheses,
    canonical_url,
    classify_result,
    discovery_queries,
    enrichment_queries,
    mark_source_independence,
    observation_from_result,
    source_entity_id,
    source_family_for_observation,
    stable_digest,
    verification_queries,
)
from venture_intelligence.batch_discovery.models import (
    BatchCandidateDetailResponse,
    BatchDiscoveryListResponse,
    BatchDiscoveryObservation,
    BatchDiscoveryReplayResult,
    BatchDiscoveryRequest,
    BatchDiscoveryRun,
    BatchSourceOutcome,
    CandidateHypothesis,
    DiscoveryDropReason,
    DiscoveryFunnel,
    DiscoveryStage,
    DiscoveryZeroState,
    FundingClaimKind,
    FundingStatus,
    ManualBatchObservationRequest,
    ObservationStance,
    SourceIndependence,
    SourceTaxonomyRole,
    SourceYieldMetrics,
    WatchlistYieldMetrics,
)
from venture_intelligence.batch_discovery.repository import BatchDiscoveryRepository
from venture_intelligence.config import Settings, settings
from venture_intelligence.crawler import StealthCrawler
from venture_intelligence.programs import ProgramConfig, ProgramRegistry, get_program_registry
from venture_intelligence.research.request_budget import RequestBudgetTracker
from venture_intelligence.research.sources import (
    SearchProvider,
    SearchResult,
    extract_venture_metadata,
    public_document_text,
)
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EntityType,
    EventType,
    ResearchBudget,
    Source,
    SourceOperationStatus,
    TemporalPrecision,
)

_NAMESPACE = uuid.UUID("b58a7742-4b6a-5bbf-b1d2-d65856840147")
_RESTRICTED = {
    "linkedin.com",
    "x.com",
    "twitter.com",
    "instagram.com",
    "threads.net",
}
_TECHNICAL = {"github.com", "huggingface.co", "pypi.org", "npmjs.com", "arxiv.org", "orcid.org"}


class BatchDiscoveryService:
    """Discover unknown candidates without allowing leads into canonical state."""

    def __init__(
        self,
        repository: BatchDiscoveryRepository,
        providers: SearchProvider | Sequence[SearchProvider],
        *,
        crawler: StealthCrawler | None = None,
        registry: ProgramRegistry | None = None,
        runtime_settings: Settings = settings,
    ) -> None:
        self.repository = repository
        self.providers = list(providers) if isinstance(providers, (list, tuple)) else [providers]
        self.crawler = crawler
        self.registry = registry or get_program_registry()
        self.settings = runtime_settings

    def default_budget(self) -> ResearchBudget:
        return ResearchBudget(
            max_queries=min(24, self.settings.research_max_queries),
            max_depth=min(2, self.settings.research_max_depth),
            max_results_per_query=self.settings.research_max_results_per_query,
            max_runtime_seconds=self.settings.research_max_runtime_seconds,
            max_total_requests=self.settings.research_max_total_requests,
            max_requests_per_provider=self.settings.research_max_requests_per_provider,
            max_retry_requests=self.settings.research_max_retry_requests,
            max_requests_per_domain=self.settings.research_max_requests_per_domain,
        )

    async def run(
        self,
        request: BatchDiscoveryRequest,
        *,
        event_bus: EventBus | None = None,
        observed_at: datetime | None = None,
    ) -> BatchDiscoveryRun:
        program = self._program(request.program_id, request.batch_id)
        scope_id = program.id if program else "generic_venture"
        scope_batch = program.batch if program else "unscoped"
        timestamp = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        budget = request.budget or self.default_budget()
        run = BatchDiscoveryRun(
            program_id=scope_id,
            batch_id=scope_batch,
            budget=budget,
            started_at=timestamp,
        )
        known_before = {
            item.hypothesis_id: item
            for item in self.repository.latest_hypotheses(
                scope_id, scope_batch, include_rejected=True
            )
        }
        tracker = RequestBudgetTracker(budget)
        for provider in self.providers:
            setter = getattr(provider, "set_request_budget", None)
            if setter is not None:
                setter(tracker)

        collected: dict[str, BatchDiscoveryObservation] = {}
        watchlist_counts: dict[tuple[str, str, str], Counter[str]] = {}
        fetched_urls: set[str] = set()
        query_count = 0
        query_budget_reached = False
        directory_host = (
            (urlsplit(program.directory_url).hostname or "").casefold().removeprefix("www.")
            if program is not None and program.directory_url
            else ""
        )
        # Blind discovery excludes official cohort/directory hosts across the
        # registry, including programs selected later and historical cohorts.
        # Talent hosts also cover triangulation programs without a directory URL.
        directory_hosts = {
            (urlsplit(url).hostname or "").casefold().rstrip(".").removeprefix("www.")
            for configured in self.registry.all()
            for url in (
                configured.directory_url,
                configured.talent_page_url,
                *(batch.directory_url for batch in configured.supported_batches),
            )
            if url
        }

        async def execute(
            stage: DiscoveryStage,
            query: str,
            hint: CandidateHypothesis | None = None,
        ) -> None:
            nonlocal query_budget_reached, query_count
            if tracker.stop_reason == "timeout":
                query_budget_reached = True
                return
            if query_count >= budget.max_queries:
                query_budget_reached = True
                return
            query_count += 1
            run.queries.append(query)
            query_source_family = self._query_source_family(query, stage, directory_host)
            for provider in self.providers:
                policy = getattr(provider, "policy", None)
                provider_id = str(
                    getattr(policy, "provider_id", None)
                    or getattr(provider, "domain", provider.__class__.__name__)
                )
                is_indexed_social_query = any(
                    marker in query.casefold()
                    for marker in (
                        "site:linkedin.com",
                        "site:x.com",
                        "site:instagram.com",
                        "site:threads.net",
                    )
                )
                if is_indexed_social_query and provider_id in {
                    "techcrunch",
                    "newsletter_watchlist",
                    "vc_watchlist",
                }:
                    # Feeds and configured public pages are not search indexes;
                    # querying them with social operators wastes the shared live
                    # request budget and provides no platform validation.
                    continue
                source_family = {
                    "public_program_osint": "indexed_social",
                    "techcrunch": "techcrunch",
                    "newsletter_watchlist": "newsletter_substack",
                    "vc_watchlist": "vc_partner",
                    "arxiv": "technical_publication",
                    "wayback": "historical_archive",
                }.get(provider_id, query_source_family)
                started = time.monotonic()
                try:
                    remaining = max(
                        0.01,
                        budget.max_runtime_seconds - (time.monotonic() - tracker.started_at),
                    )
                    response = await asyncio.wait_for(
                        provider.search(query, budget.max_results_per_query),
                        timeout=remaining,
                    )
                except TimeoutError:
                    query_budget_reached = True
                    tracker.stop_reason = "timeout"
                    self._record_outcome(
                        run,
                        provider_id,
                        source_family,
                        stage,
                        SourceOperationStatus.TIMEOUT,
                        latency_ms=(time.monotonic() - started) * 1_000,
                        error="bounded discovery runtime exhausted",
                    )
                    continue
                except Exception as error:
                    self._record_outcome(
                        run,
                        provider_id,
                        source_family,
                        stage,
                        SourceOperationStatus.FAILED,
                        latency_ms=(time.monotonic() - started) * 1_000,
                        error=str(error)[:500],
                    )
                    continue
                accepted = 0
                relevant = 0
                rejection_reasons: Counter[DiscoveryDropReason] = Counter()
                platform_metrics: dict[str, Counter[str]] = {}
                for result in response.results:
                    watchlist_key: tuple[str, str, str] | None = None
                    if provider_id in {
                        "techcrunch",
                        "newsletter_watchlist",
                        "vc_watchlist",
                    }:
                        metadata = result.metadata
                        watchlist_key = (
                            provider_id,
                            str(metadata.get("watchlist_id") or result.source_domain),
                            str(
                                metadata.get("publication")
                                or metadata.get("author")
                                or result.source_domain
                            ),
                        )
                        watchlist_counts.setdefault(watchlist_key, Counter())["items_checked"] += 1
                        watchlist_counts[watchlist_key]["new_items"] += 1
                    result_host = (
                        (urlsplit(result.url).hostname or "").casefold().removeprefix("www.")
                    )
                    platform = {
                        "linkedin.com": "linkedin",
                        "x.com": "x",
                        "twitter.com": "x",
                        "instagram.com": "instagram",
                        "threads.net": "threads",
                    }.get(result_host)
                    if platform:
                        platform_metrics.setdefault(platform, Counter())["retrieved"] += 1
                    if (
                        stage == DiscoveryStage.DISCOVERY
                        and result_host.rstrip(".") in directory_hosts
                    ):
                        # The official directory can verify an existing hypothesis, but
                        # it must never seed the M8 unknown-candidate discovery set.
                        rejection_reasons[DiscoveryDropReason.ALREADY_KNOWN] += 1
                        continue
                    observation, drop_reason, is_relevant = classify_result(
                        result=result,
                        query=query,
                        provider_id=provider_id,
                        run_id=run.run_id,
                        program=program,
                        observed_at=timestamp,
                        hint=hint,
                    )
                    relevant += int(is_relevant)
                    if platform and is_relevant:
                        platform_metrics[platform]["relevant"] += 1
                    if watchlist_key is not None and is_relevant:
                        watchlist_counts[watchlist_key]["relevant_items"] += 1
                    if (
                        observation is None
                        and is_relevant
                        and result.url not in fetched_urls
                    ):
                        fetched_urls.add(result.url)
                        enriched = await self._verify_public_result(result, None, tracker)
                        if enriched is not None:
                            observation, drop_reason, _ = classify_result(
                                result=enriched,
                                query=query,
                                provider_id="direct-public-extractor",
                                run_id=run.run_id,
                                program=program,
                                observed_at=timestamp,
                                hint=hint,
                            )
                    if observation is None:
                        if drop_reason is not None:
                            rejection_reasons[drop_reason] += 1
                            if platform:
                                platform_metrics[platform]["rejected"] += 1
                        continue
                    observation = observation.model_copy(
                        update={
                            "provenance": {
                                **observation.provenance,
                                "discovery_stage": stage.value,
                                "watchlist_key": list(watchlist_key) if watchlist_key else None,
                            }
                        }
                    )
                    duplicate = observation.observation_id in collected
                    collected[observation.observation_id] = self._merge_observation(
                        collected.get(observation.observation_id), observation
                    )
                    if duplicate:
                        rejection_reasons[DiscoveryDropReason.DERIVATIVE_DUPLICATE] += 1
                        if platform:
                            platform_metrics[platform]["duplicates"] += 1
                        if watchlist_key is not None:
                            watchlist_counts[watchlist_key]["duplicates"] += 1
                    else:
                        accepted += 1
                        if platform:
                            platform_metrics[platform]["observations"] += 1
                        if watchlist_key is not None:
                            counts = watchlist_counts[watchlist_key]
                            counts["venture_observations"] += 1
                            metadata = observation.raw_observation.get("metadata", {})
                            counts["funding_claims"] += int(
                                bool(metadata.get("funding_claim_kind"))
                            )
                            counts["investor_claims"] += int(bool(metadata.get("investors")))
                    if stage is DiscoveryStage.DISCOVERY and result.url not in fetched_urls:
                        fetched_urls.add(result.url)
                        verified = await self._verify_public_result(
                            result,
                            observation,
                            tracker,
                        )
                        if verified is not None:
                            verified_observation = observation_from_result(
                                result=verified,
                                query=query,
                                provider_id="direct-public-verifier",
                                run_id=run.run_id,
                                program=program,
                                observed_at=timestamp,
                                hint=None,
                            )
                            if verified_observation is not None:
                                collected[verified_observation.observation_id] = (
                                    self._merge_observation(
                                        collected.get(verified_observation.observation_id),
                                        verified_observation,
                                    )
                                )
                                accepted += 1
                self._record_outcome(
                    run,
                    provider_id,
                    source_family,
                    stage,
                    response.operation_status or SourceOperationStatus.FAILED,
                    results=len(response.results),
                    accepted=accepted,
                    relevant=relevant,
                    rejection_reasons=dict(rejection_reasons),
                    platform_metrics={
                        platform: dict(counts)
                        for platform, counts in platform_metrics.items()
                    },
                    request_attempts=response.attempts,
                    retry_requests=response.retry_requests,
                    redirect_requests=response.redirect_requests,
                    latency_ms=(time.monotonic() - started) * 1_000,
                    error=response.error,
                )

        run.stage = DiscoveryStage.DISCOVERY
        for query in discovery_queries(program):
            await execute(DiscoveryStage.DISCOVERY, query)

        existing = self.repository.observations(scope_id, scope_batch)
        initial_observations = self._combined(existing, list(collected.values()))
        initial = build_hypotheses(
            initial_observations,
            run_id=run.run_id,
            program=program,
            revision_for=lambda _hypothesis_id: 1,
        )

        run.stage = DiscoveryStage.VERIFICATION
        for candidate in initial[:8]:
            for query in verification_queries(
                program,
                candidate,
                include_directory=request.include_directory_verification,
            ):
                await execute(DiscoveryStage.VERIFICATION, query, candidate)

        verified_observations = self._combined(existing, list(collected.values()))
        verified_candidates = build_hypotheses(
            verified_observations,
            run_id=run.run_id,
            program=program,
            revision_for=lambda _hypothesis_id: 1,
        )

        run.stage = DiscoveryStage.ENRICHMENT
        enrichment_candidates = [
            item for item in verified_candidates if item.assessment.confidence_pct >= 40
        ][:6]
        for candidate in enrichment_candidates:
            for query in enrichment_queries(candidate):
                await execute(DiscoveryStage.ENRICHMENT, query, candidate)

        all_observations = self._combined(existing, list(collected.values()))
        final = build_hypotheses(
            all_observations,
            run_id=run.run_id,
            program=program,
            revision_for=self.repository.next_revision,
        )
        final = [self._with_run_coverage(item, run) for item in final]
        for observation in all_observations:
            if observation.run_id != run.run_id:
                continue
            self.repository.save_observation(observation, self._hypothesis_key(observation))

        persisted: list[CandidateHypothesis] = []
        promoted = 0
        for candidate in final:
            updated = candidate
            if (
                program is not None
                and candidate.assessment.promotion_eligible
                and event_bus is not None
            ):
                person_id, company_id, emitted = self._promote(
                    candidate,
                    all_observations,
                    program,
                    event_bus,
                )
                promoted += int(emitted > 0)
                updated = candidate.model_copy(
                    update={
                        "promoted_person_source_id": person_id,
                        "promoted_company_source_id": company_id,
                    }
                )
            persisted.append(self.repository.save_hypothesis(updated))

        snapshot = tracker.snapshot()
        run.request_accounting = snapshot
        run.stage = DiscoveryStage.COMPLETED
        run.status = "completed"
        run.stop_reason = str(
            snapshot.get("stop_reason")
            or ("max_queries_reached" if query_budget_reached else "completed")
        )
        if run.stop_reason != "completed":
            run.status = "partial"
        run.observations = [item for item in all_observations if item.run_id == run.run_id]
        run.hypotheses = persisted
        run.promoted_count = promoted
        run.funnel = self._funnel(
            run,
            persisted,
            known_before,
            all_observations,
            watchlist_counts,
        )
        run.zero_state = self._zero_state(run)
        run.finished_at = timestamp if observed_at is not None else datetime.now(timezone.utc)
        run.input_digest = stable_digest(
            {
                "program_id": scope_id,
                "batch_id": scope_batch,
                "queries": run.queries,
                "observations": [item.model_dump(mode="json") for item in all_observations],
            }
        )
        self.repository.save_run(run)
        return run

    def list_candidates(
        self,
        program_id: str | None,
        *,
        batch_id: str | None = None,
        include_rejected: bool = True,
    ) -> BatchDiscoveryListResponse:
        program = self._program(program_id, batch_id)
        scope_id = program.id if program else "generic_venture"
        scope_batch = program.batch if program else "unscoped"
        return BatchDiscoveryListResponse(
            program_id=scope_id,
            batch_id=scope_batch,
            candidates=self.repository.latest_hypotheses(
                scope_id,
                scope_batch,
                include_rejected=include_rejected,
            ),
        )

    def candidate(self, hypothesis_id: str) -> CandidateHypothesis | None:
        return self.repository.hypothesis(hypothesis_id)

    def candidate_detail(self, hypothesis_id: str) -> BatchCandidateDetailResponse | None:
        candidate = self.repository.hypothesis(hypothesis_id)
        if candidate is None:
            return None
        return BatchCandidateDetailResponse(
            candidate=candidate,
            observations=self.repository.observations_by_ids(candidate.observation_ids),
        )

    def replay(
        self,
        program_id: str | None,
        cutoff: datetime,
    ) -> BatchDiscoveryReplayResult:
        program = self._program(program_id)
        scope_id = program.id if program else "generic_venture"
        scope_batch = program.batch if program else "unscoped"
        cutoff = cutoff.astimezone(timezone.utc)
        before = self.repository.state_counts()
        observations = mark_source_independence(
            self.repository.observations(scope_id, scope_batch, cutoff=cutoff)
        )
        hypotheses = build_hypotheses(
            observations,
            run_id=f"replay-{cutoff.isoformat()}",
            program=program,
            revision_for=lambda _hypothesis_id: 1,
        )
        eligible_runs = [
            item
            for item in self.repository.runs(program_id=scope_id, limit=200)
            if item.batch_id == scope_batch and item.started_at <= cutoff
        ]
        if eligible_runs:
            coverage_run = max(eligible_runs, key=lambda item: item.started_at)
            hypotheses = [
                self._with_run_coverage(item, coverage_run) for item in hypotheses
            ]
        after = self.repository.state_counts()
        if before != after:
            raise RuntimeError("batch discovery replay mutated live state")
        return BatchDiscoveryReplayResult(
            program_id=scope_id,
            batch_id=scope_batch,
            cutoff=cutoff,
            observations_selected=len(observations),
            hypotheses=hypotheses,
            input_digest=stable_digest([item.model_dump(mode="json") for item in observations]),
        )

    def ingest_manual(
        self,
        request: ManualBatchObservationRequest,
        *,
        event_bus: EventBus | None = None,
        observed_at: datetime | None = None,
    ) -> CandidateHypothesis:
        program = self._program(request.program_id)
        if not request.source_url.startswith(("http://", "https://")):
            raise ValueError("manual observation source_url must be an HTTP(S) public URL")
        timestamp = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        run_id = f"manual-{uuid.uuid4()}"
        domain = (urlsplit(request.source_url).hostname or "").casefold().removeprefix("www.")
        identity = request.candidate_name or request.company_name or "candidate"
        observation = BatchDiscoveryObservation(
            observation_id=str(
                uuid.uuid5(
                    _NAMESPACE,
                    f"manual|{program.id}|{canonical_url(request.source_url)}|{identity}|{request.stance}",
                )
            ),
            run_id=run_id,
            program_id=program.id,
            batch_id=program.batch,
            candidate_kind=request.candidate_kind,
            candidate_name=request.candidate_name,
            company_name=request.company_name,
            claim=request.claim,
            stance=request.stance,
            source_roles=request.source_roles,
            source_url=request.source_url,
            canonical_url=canonical_url(request.source_url),
            source_domain=domain,
            source_provider="manual-import",
            query="",
            origin_key=stable_digest(canonical_url(request.source_url)),
            origin_url=canonical_url(request.source_url),
            publisher=domain,
            claim_fingerprint=stable_digest(
                {
                    "identity": identity.casefold(),
                    "claim": re.sub(r"\s+", " ", request.claim.casefold()).strip(),
                }
            ),
            independence=SourceIndependence.UNKNOWN,
            source_reliability=0.72,
            identity_confidence=(
                0.86 if SourceTaxonomyRole.IDENTITY in request.source_roles else 0.68
            ),
            directness=0.82,
            temporal_relevance=0.9,
            published_at=request.published_at,
            observed_at=timestamp,
            retrieved_at=timestamp,
            aliases=request.aliases,
            usernames=request.usernames,
            domains=request.domains,
            cofounders=request.cofounders,
            provenance={
                **request.provenance,
                "access_mode": "manual_import",
                "network_request_made": False,
            },
            raw_observation={"manual": True},
        )
        observations = self._combined(
            self.repository.observations(program.id, program.batch),
            [observation],
        )
        persisted_observation = next(
            item for item in observations if item.observation_id == observation.observation_id
        )
        self.repository.save_observation(
            persisted_observation,
            self._hypothesis_key(persisted_observation),
        )
        candidates = build_hypotheses(
            observations,
            run_id=run_id,
            program=program,
            revision_for=self.repository.next_revision,
        )
        matching = next(
            item for item in candidates if observation.observation_id in item.observation_ids
        )
        if matching.assessment.promotion_eligible and event_bus is not None:
            person_id, company_id, _ = self._promote(
                matching,
                observations,
                program,
                event_bus,
            )
            matching = matching.model_copy(
                update={
                    "promoted_person_source_id": person_id,
                    "promoted_company_source_id": company_id,
                }
            )
        return self.repository.save_hypothesis(matching)

    async def close(self) -> None:
        closed: set[int] = set()
        for provider in self.providers:
            close = getattr(provider, "close", None)
            if close is None or id(provider) in closed:
                continue
            closed.add(id(provider))
            result: Any = close()
            if hasattr(result, "__await__"):
                await result
        if self.crawler is not None and id(self.crawler) not in closed:
            await self.crawler.close()

    async def _verify_public_result(
        self,
        result: SearchResult,
        observation: BatchDiscoveryObservation | None,
        tracker: RequestBudgetTracker,
    ) -> SearchResult | None:
        if self.crawler is None or result.direct_public:
            return None
        domain = (
            (urlsplit(result.url).hostname or result.source_domain)
            .casefold()
            .removeprefix("www.")
        )
        if domain in _RESTRICTED or domain in _TECHNICAL:
            return None
        response = await self.crawler.fetch_text_budgeted(
            result.url,
            tracker,
            "batch-public-verification",
        )
        if not response.ok or not response.text:
            return None
        text = public_document_text(response.text)[:4_000]
        extracted = extract_venture_metadata(
            result.title,
            text,
            roles=["DISCOVERY", "IDENTITY"],
        )
        if observation is not None:
            names = [observation.candidate_name, observation.company_name]
            if not any(name and name.casefold() in text.casefold() for name in names):
                return None
            extracted.update(
                {
                    "source_roles": ["IDENTITY", "VERIFICATION"],
                    "identity_confidence": max(observation.identity_confidence, 0.88),
                    "source_reliability": 0.86,
                    "directness": 0.9,
                    "canonical_origin_url": observation.canonical_url,
                }
            )
        return result.model_copy(
            update={
                "snippet": text,
                "direct_public": True,
                "metadata": {
                    **result.metadata,
                    **extracted,
                },
                "provenance": {
                    **result.provenance,
                    "adapter": "bounded-direct-public-verifier",
                    "final_url": response.url,
                    "http_status": response.status_code,
                },
            }
        )

    @staticmethod
    def _combined(
        existing: list[BatchDiscoveryObservation],
        new: list[BatchDiscoveryObservation],
    ) -> list[BatchDiscoveryObservation]:
        unique: dict[str, BatchDiscoveryObservation] = {}
        for item in [*existing, *new]:
            unique[item.observation_id] = BatchDiscoveryService._merge_observation(
                unique.get(item.observation_id), item
            )
        return mark_source_independence(list(unique.values()))

    @staticmethod
    def _merge_observation(
        existing: BatchDiscoveryObservation | None,
        incoming: BatchDiscoveryObservation,
    ) -> BatchDiscoveryObservation:
        if existing is None:
            providers = list(
                dict.fromkeys([*incoming.acquisition_providers, incoming.source_provider])
            )
            return incoming.model_copy(
                update={
                    "acquisition_providers": providers,
                    "provenance": {
                        **incoming.provenance,
                        "acquisition_providers": providers,
                        "acquisition_paths": incoming.provenance.get(
                            "acquisition_paths",
                            [
                                {
                                    "provider": incoming.source_provider,
                                    "query": incoming.query,
                                    "result_url": incoming.source_url,
                                }
                            ],
                        ),
                    },
                }
            )
        providers = list(
            dict.fromkeys(
                [
                    *existing.acquisition_providers,
                    existing.source_provider,
                    *incoming.acquisition_providers,
                    incoming.source_provider,
                ]
            )
        )
        paths = [
            *existing.provenance.get(
                "acquisition_paths",
                [
                    {
                        "provider": existing.source_provider,
                        "query": existing.query,
                        "result_url": existing.source_url,
                    }
                ],
            ),
            *incoming.provenance.get(
                "acquisition_paths",
                [
                    {
                        "provider": incoming.source_provider,
                        "query": incoming.query,
                        "result_url": incoming.source_url,
                    }
                ],
            ),
        ]
        unique_paths = list(
            {
                (
                    str(item.get("provider")),
                    str(item.get("query")),
                    str(item.get("result_url")),
                ): item
                for item in paths
                if isinstance(item, dict)
            }.values()
        )
        return existing.model_copy(
            update={
                "acquisition_providers": providers,
                "provenance": {
                    **existing.provenance,
                    "acquisition_providers": providers,
                    "acquisition_paths": unique_paths,
                },
            }
        )

    @staticmethod
    def _record_outcome(
        run: BatchDiscoveryRun,
        provider_id: str,
        source_family: str,
        stage: DiscoveryStage,
        status: SourceOperationStatus,
        *,
        results: int = 0,
        accepted: int = 0,
        relevant: int = 0,
        rejection_reasons: dict[DiscoveryDropReason, int] | None = None,
        platform_metrics: dict[str, dict[str, int]] | None = None,
        error: str | None = None,
        request_attempts: int = 0,
        retry_requests: int = 0,
        redirect_requests: int = 0,
        latency_ms: float = 0,
    ) -> None:
        existing = next(
            (
                item
                for item in run.source_outcomes
                if item.provider_id == provider_id
                and item.source_family == source_family
                and item.stage == stage
            ),
            None,
        )
        if existing is None:
            run.source_outcomes.append(
                BatchSourceOutcome(
                    provider_id=provider_id,
                    source_family=source_family,
                    stage=stage,
                    status=status,
                    queries_attempted=1,
                    results_seen=results,
                    observations_accepted=accepted,
                    relevant_items=relevant,
                    rejection_reasons=rejection_reasons or {},
                    platform_metrics=platform_metrics or {},
                    request_attempts=request_attempts,
                    retry_requests=retry_requests,
                    redirect_requests=redirect_requests,
                    latency_ms=latency_ms,
                    error=error,
                )
            )
            return
        existing.queries_attempted += 1
        existing.results_seen += results
        existing.observations_accepted += accepted
        existing.relevant_items += relevant
        for reason, count in (rejection_reasons or {}).items():
            existing.rejection_reasons[reason] = existing.rejection_reasons.get(reason, 0) + count
        for platform, counts in (platform_metrics or {}).items():
            target = existing.platform_metrics.setdefault(platform, {})
            for metric, count in counts.items():
                target[metric] = target.get(metric, 0) + count
        existing.request_attempts += request_attempts
        existing.retry_requests += retry_requests
        existing.redirect_requests += redirect_requests
        existing.latency_ms += latency_ms
        if status in {SourceOperationStatus.SUCCESS, SourceOperationStatus.PARTIAL}:
            existing.status = status
        elif existing.status not in {SourceOperationStatus.SUCCESS, SourceOperationStatus.PARTIAL}:
            existing.status = status
        if error:
            existing.error = error

    @staticmethod
    def _with_run_coverage(
        candidate: CandidateHypothesis,
        run: BatchDiscoveryRun,
    ) -> CandidateHypothesis:
        coverage = candidate.assessment.source_coverage.model_copy(deep=True)
        by_family = {item.family: item for item in coverage.families}
        provider_families = {
            "duckduckgo": "public_search",
            "bing": "public_search",
            "yahoo": "public_search",
            "brave": "public_search",
            "public_program_osint": "indexed_social",
            "techcrunch": "techcrunch",
            "newsletter_watchlist": "newsletter_substack",
            "vc_watchlist": "vc_partner",
            "arxiv": "technical_publication",
            "wayback": "historical_archive",
        }
        grouped: dict[str, list[BatchSourceOutcome]] = {}
        for outcome in run.source_outcomes:
            family = outcome.source_family or provider_families.get(outcome.provider_id)
            if family:
                grouped.setdefault(family, []).append(outcome)
        permission_states = {
            SourceOperationStatus.AUTH_REQUIRED,
            SourceOperationStatus.PERMISSION_REQUIRED,
        }
        unavailable_states = {
            SourceOperationStatus.BLOCKED,
            SourceOperationStatus.CIRCUIT_OPEN,
            SourceOperationStatus.FAILED,
            SourceOperationStatus.NETWORK_ERROR,
            SourceOperationStatus.PARSER_ERROR,
            SourceOperationStatus.RATE_LIMITED,
            SourceOperationStatus.TEMPORARILY_UNAVAILABLE,
            SourceOperationStatus.TIMEOUT,
            SourceOperationStatus.UNAVAILABLE,
        }
        for family, outcomes in grouped.items():
            item = by_family.get(family)
            if item is None or not item.applicable or item.status != "not_checked":
                continue
            if any(
                outcome.status
                in {
                    SourceOperationStatus.SUCCESS,
                    SourceOperationStatus.PARTIAL,
                    SourceOperationStatus.EMPTY,
                }
                for outcome in outcomes
            ):
                item.status = "checked_no_evidence"
                item.checked_no_evidence = True
            elif any(outcome.status in permission_states for outcome in outcomes):
                item.status = "permission_required"
                item.unavailable_reason = "permission_required"
            elif outcomes and all(outcome.status in unavailable_states for outcome in outcomes):
                item.status = "unavailable"
                item.unavailable_reason = next(
                    (outcome.error for outcome in outcomes if outcome.error),
                    "source_unavailable",
                )
        coverage.checked_families = sum(
            item.applicable
            and item.status not in {"not_checked", "unavailable", "permission_required"}
            for item in coverage.families
        )
        coverage.unavailable_families = sum(
            item.status == "unavailable" for item in coverage.families
        )
        coverage.permission_required_families = sum(
            item.status == "permission_required" for item in coverage.families
        )
        coverage.coverage_pct = round(
            100 * coverage.checked_families / coverage.applicable_families, 2
        )
        next_checks = [
            f"Check {item.family}: {item.applicability_reason}"
            for item in coverage.families
            if item.applicable and item.status == "not_checked"
        ][:3]
        label = (
            "HIGH CONFIDENCE · LOW COVERAGE"
            if candidate.assessment.confidence_pct >= 72 and coverage.coverage_pct < 50
            else "LOW CONFIDENCE · HIGH COVERAGE"
            if candidate.assessment.confidence_pct < 50 and coverage.coverage_pct >= 75
            else "ADEQUATE COVERAGE"
            if coverage.coverage_pct >= 50
            else "LOW COVERAGE"
        )
        return candidate.model_copy(
            update={
                "assessment": candidate.assessment.model_copy(
                    update={
                        "source_coverage": coverage,
                        "coverage_label": label,
                        "next_best_checks": next_checks,
                    }
                )
            }
        )

    @staticmethod
    def _funnel(
        run: BatchDiscoveryRun,
        hypotheses: list[CandidateHypothesis],
        known_before: dict[str, CandidateHypothesis],
        observations: list[BatchDiscoveryObservation],
        watchlist_counts: dict[tuple[str, str, str], Counter[str]],
    ) -> DiscoveryFunnel:
        created = [item for item in hypotheses if item.hypothesis_id not in known_before]
        updated = [
            item
            for item in hypotheses
            if item.hypothesis_id in known_before
            and item.input_digest != known_before[item.hypothesis_id].input_digest
        ]
        drop_reasons: Counter[DiscoveryDropReason] = Counter()
        for outcome in run.source_outcomes:
            drop_reasons.update(outcome.rejection_reasons)
        for candidate in hypotheses:
            blockers = set(candidate.assessment.promotion_blockers)
            if "fewer_than_two_independent_supporting_sources" in blockers:
                drop_reasons[DiscoveryDropReason.INSUFFICIENT_INDEPENDENT_ORIGINS] += 1
            if "no_target_program_or_batch_evidence" in blockers:
                drop_reasons[DiscoveryDropReason.NO_BATCH_EVIDENCE] += 1
            if "contradictory_or_other_batch_evidence_dominates" in blockers:
                drop_reasons[DiscoveryDropReason.DOMINANT_CONTRADICTION] += 1
            if "batch_confidence_below_72_pct" in blockers:
                drop_reasons[DiscoveryDropReason.BELOW_PROMOTION_THRESHOLD] += 1
        source_yield: list[SourceYieldMetrics] = []
        run_observations = [item for item in observations if item.run_id == run.run_id]
        for outcome in run.source_outcomes:
            owned = [
                item
                for item in run_observations
                if item.source_provider == outcome.provider_id
                and item.provenance.get("discovery_stage") == outcome.stage.value
            ]
            hypothesis_ids = {
                candidate.hypothesis_id
                for candidate in hypotheses
                if any(item.observation_id in candidate.observation_ids for item in owned)
            }
            source_yield.append(
                SourceYieldMetrics(
                    provider_id=outcome.provider_id,
                    source_family=(
                        source_family_for_observation(owned[0])
                        if owned
                        else outcome.source_family
                    ),
                    stage=outcome.stage,
                    items_retrieved=outcome.results_seen,
                    relevant_items=outcome.relevant_items,
                    venture_observations=outcome.observations_accepted,
                    candidate_hypotheses_created=sum(
                        item.hypothesis_id in hypothesis_ids for item in created
                    ),
                    candidate_hypotheses_updated=sum(
                        item.hypothesis_id in hypothesis_ids for item in updated
                    ),
                    independent_evidence_origins=len(
                        {
                            item.origin_key
                            for item in owned
                            if item.independence is SourceIndependence.INDEPENDENT
                        }
                    ),
                    promotions_contributed_to=sum(
                        item.hypothesis_id in hypothesis_ids
                        and item.assessment.promotion_eligible
                        for item in hypotheses
                    ),
                    funding_claims=sum(
                        bool(item.raw_observation.get("metadata", {}).get("funding_claim_kind"))
                        for item in owned
                    ),
                    investor_claims=sum(
                        bool(item.raw_observation.get("metadata", {}).get("investors"))
                        for item in owned
                    ),
                    duplicates=outcome.rejection_reasons.get(
                        DiscoveryDropReason.DERIVATIVE_DUPLICATE, 0
                    ),
                    rejected=sum(outcome.rejection_reasons.values()),
                    rejection_reasons=outcome.rejection_reasons,
                )
            )
        watchlist_yield: list[WatchlistYieldMetrics] = []
        for key, counts in sorted(watchlist_counts.items()):
            provider_id, watchlist_id, publication = key
            owned_ids = {
                item.observation_id
                for item in run_observations
                if tuple(item.provenance.get("watchlist_key") or ()) == key
            }
            hypothesis_ids = {
                item.hypothesis_id
                for item in hypotheses
                if owned_ids & set(item.observation_ids)
            }
            watchlist_yield.append(
                WatchlistYieldMetrics(
                    provider_id=provider_id,
                    watchlist_id=watchlist_id,
                    publication_or_actor=publication,
                    candidate_hypotheses_created=sum(
                        item.hypothesis_id in hypothesis_ids for item in created
                    ),
                    candidate_hypotheses_updated=sum(
                        item.hypothesis_id in hypothesis_ids for item in updated
                    ),
                    **dict(counts),
                )
            )
        return DiscoveryFunnel(
            retrieved_items=sum(item.results_seen for item in run.source_outcomes),
            relevant_items=sum(item.relevant_items for item in run.source_outcomes),
            venture_observations=sum(item.observations_accepted for item in run.source_outcomes),
            candidate_hypotheses_created=len(created),
            candidate_hypotheses_updated=len(updated),
            identity_corroborated=sum(
                item.assessment.identity_corroborated for item in hypotheses
            ),
            batch_evidence_present=sum(
                item.assessment.batch_evidence_present for item in hypotheses
            ),
            promotion_eligible=sum(item.assessment.promotion_eligible for item in hypotheses),
            canonical_promotions=run.promoted_count,
            drop_reasons=dict(drop_reasons),
            source_yield=source_yield,
            watchlist_yield=watchlist_yield,
        )

    @staticmethod
    def _zero_state(run: BatchDiscoveryRun) -> DiscoveryZeroState:
        if run.promoted_count:
            return DiscoveryZeroState.NOT_ZERO
        if run.hypotheses:
            return DiscoveryZeroState.THRESHOLD_ZERO
        unavailable = {
            SourceOperationStatus.BLOCKED,
            SourceOperationStatus.CIRCUIT_OPEN,
            SourceOperationStatus.FAILED,
            SourceOperationStatus.NETWORK_ERROR,
            SourceOperationStatus.RATE_LIMITED,
            SourceOperationStatus.TIMEOUT,
            SourceOperationStatus.UNAVAILABLE,
        }
        if any(item.status in unavailable for item in run.source_outcomes):
            return DiscoveryZeroState.DEGRADED_ZERO
        if run.funnel.relevant_items and not run.funnel.venture_observations:
            return DiscoveryZeroState.PIPELINE_ZERO
        return DiscoveryZeroState.HEALTHY_ZERO

    @staticmethod
    def _query_source_family(
        query: str, stage: DiscoveryStage, directory_host: str
    ) -> str:
        normalized = query.casefold()
        if any(
            target in normalized
            for target in (
                "site:linkedin.com",
                "site:x.com",
                "site:twitter.com",
                "site:instagram.com",
                "site:threads.net",
            )
        ):
            return "indexed_social"
        if directory_host and f"site:{directory_host}" in normalized:
            return "official_program"
        technical_targets = (
            "site:arxiv.org",
            "site:github.com",
            "site:orcid.org",
            "site:huggingface.co",
            "site:pypi.org",
            "site:npmjs.com",
        )
        if any(target in normalized for target in technical_targets):
            return "technical_publication"
        if "site:web.archive.org" in normalized:
            return "historical_archive"
        if "site:techcrunch.com" in normalized:
            return "techcrunch"
        if "site:substack.com" in normalized:
            return "newsletter_substack"
        if stage is DiscoveryStage.VERIFICATION:
            return "general_public_web"
        return "public_search"

    def _program(
        self,
        program_id: str | None,
        batch_id: str | None = None,
    ) -> ProgramConfig | None:
        if program_id is None:
            if batch_id is not None:
                raise ValueError("batch_id requires a configured program_id")
            return None
        program = self.registry.get(program_id)
        if program is None or not program.active:
            raise LookupError(f"Unknown active program id: {program_id}")
        selected = self.registry.batch(program_id, batch_id)
        if selected is None:
            raise LookupError(f"Unknown batch {batch_id!r} for active program id: {program_id}")
        if selected.id != program.batch:
            program = program.model_copy(
                update={
                    "batch": selected.id,
                    "current_batch": selected.id,
                    "directory_url": selected.directory_url or program.directory_url,
                    "keywords": selected.keywords or program.keywords,
                }
            )
        return program

    @staticmethod
    def _hypothesis_key(observation: BatchDiscoveryObservation) -> str:
        person = re.sub(r"[^a-z0-9]+", " ", (observation.candidate_name or "").casefold()).strip()
        company = re.sub(r"[^a-z0-9]+", " ", (observation.company_name or "").casefold()).strip()
        return f"person:{person}|company:{company}" if person else f"company:{company}"

    @staticmethod
    def _promote(
        candidate: CandidateHypothesis,
        observations: list[BatchDiscoveryObservation],
        program: ProgramConfig,
        event_bus: EventBus,
    ) -> tuple[str | None, str | None, int]:
        evidence = [
            item for item in observations if item.observation_id in candidate.observation_ids
        ]
        supporting = [
            item
            for item in evidence
            if item.stance is ObservationStance.SUPPORTS
            and item.independence is SourceIndependence.INDEPENDENT
        ]
        if not candidate.assessment.promotion_eligible or len(supporting) < 2:
            return None, None, 0
        source = next(
            (
                item
                for item in supporting
                if SourceTaxonomyRole.VERIFICATION in item.source_roles
                and item.source_domain not in _RESTRICTED
            ),
            supporting[0],
        )
        occurred_at = source.published_at or source.observed_at
        precision = (
            TemporalPrecision.EXACT
            if source.published_at is not None
            else TemporalPrecision.OBSERVED_FALLBACK
        )
        common_metadata = {
            "program_id": program.id,
            "batch_id": program.batch,
            "candidate_hypothesis_id": candidate.hypothesis_id,
            "batch_acceptance_confidence": candidate.assessment.confidence_pct / 100,
            "identity_validated": True,
            "identity_evidence": True,
            "identity_contract": "validated_batch_hypothesis",
            "identity_evidence_urls": [
                item.canonical_url
                for item in evidence
                if SourceTaxonomyRole.IDENTITY in item.source_roles
            ],
            "supporting_source_count": candidate.assessment.independent_supporting_sources,
            "evidence_tier": "corroborated_public_source",
            "assessment": candidate.assessment.model_dump(mode="json"),
            "provenance": {
                "observation_ids": candidate.observation_ids,
                "source_diversity": candidate.assessment.source_diversity,
                "pipeline": (
                    "raw_observation->candidate_hypothesis->batch_assessment->canonical_event"
                ),
            },
        }
        events: list[CanonicalEvent] = []
        company_source_id = None
        if candidate.company_name:
            company_source_id = source_entity_id(program, "company", candidate.company_name)
            events.append(
                CanonicalEvent(
                    event_id=str(uuid.uuid5(_NAMESPACE, f"{candidate.input_digest}|company")),
                    event_type=EventType.COMPANY_LISTED,
                    entity_type=EntityType.COMPANY,
                    entity_id=company_source_id,
                    source=Source.RESEARCH_AGENT,
                    source_url=source.canonical_url,
                    occurred_at=occurred_at,
                    occurred_at_precision=precision,
                    observed_at=source.observed_at,
                    ingested_at=source.observed_at,
                    confidence=candidate.assessment.confidence_pct / 100,
                    raw_payload={
                        "name": candidate.company_name,
                        "slug": re.sub(
                            r"[^a-z0-9]+",
                            "-",
                            candidate.company_name.casefold(),
                        ).strip("-"),
                        "website": candidate.profile.website,
                        "batch": program.batch,
                    },
                    metadata=common_metadata,
                )
            )
        person_source_id = None
        if candidate.candidate_name:
            person_source_id = source_entity_id(program, "person", candidate.candidate_name)
            payload = {
                "name": candidate.candidate_name,
                "company_name": candidate.company_name,
                "headline": (
                    f"Founder at {candidate.company_name}" if candidate.company_name else None
                ),
                "profile_url": candidate.profile.linkedin_url,
                "github_username": candidate.profile.github_username,
                "batch": program.batch,
            }
            events.extend(
                [
                    CanonicalEvent(
                        event_id=str(uuid.uuid5(_NAMESPACE, f"{candidate.input_digest}|founder")),
                        event_type=EventType.FOUNDER_DETECTED,
                        entity_type=EntityType.PERSON,
                        entity_id=person_source_id,
                        source=Source.RESEARCH_AGENT,
                        source_url=source.canonical_url,
                        occurred_at=occurred_at,
                        occurred_at_precision=precision,
                        observed_at=source.observed_at,
                        ingested_at=source.observed_at,
                        confidence=candidate.assessment.confidence_pct / 100,
                        raw_payload=payload,
                        metadata=common_metadata,
                    ),
                    CanonicalEvent(
                        event_id=str(
                            uuid.uuid5(
                                _NAMESPACE,
                                f"{candidate.input_digest}|membership",
                            )
                        ),
                        event_type=EventType.PROGRAM_MEMBERSHIP_ADDED,
                        entity_type=EntityType.PERSON,
                        entity_id=person_source_id,
                        source=Source.RESEARCH_AGENT,
                        source_url=source.canonical_url,
                        occurred_at=occurred_at,
                        occurred_at_precision=precision,
                        observed_at=source.observed_at,
                        ingested_at=source.observed_at,
                        confidence=candidate.assessment.confidence_pct / 100,
                        raw_payload=payload,
                        metadata=common_metadata,
                    ),
                ]
            )
        if company_source_id:
            for index, funding in enumerate(candidate.profile.funding_rounds):
                if funding.claim_kind is not FundingClaimKind.ROUND_AMOUNT or funding.status in {
                    FundingStatus.TARGETING,
                    FundingStatus.RUMORED,
                }:
                    continue
                funding_source = next(
                    (item for item in evidence if item.canonical_url == funding.source_url),
                    source,
                )
                funding_occurred_at = funding.announced_at or funding_source.observed_at
                events.append(
                    CanonicalEvent(
                        event_id=str(
                            uuid.uuid5(
                                _NAMESPACE,
                                f"{candidate.input_digest}|funding|{index}|{funding.source_url}",
                            )
                        ),
                        event_type=EventType.FUNDING_ANNOUNCED,
                        entity_type=EntityType.COMPANY,
                        entity_id=company_source_id,
                        source=Source.RESEARCH_AGENT,
                        source_url=funding.source_url,
                        occurred_at=funding_occurred_at,
                        occurred_at_precision=(
                            TemporalPrecision.EXACT
                            if funding.announced_at is not None
                            else TemporalPrecision.OBSERVED_FALLBACK
                        ),
                        observed_at=funding_source.observed_at,
                        ingested_at=funding_source.observed_at,
                        confidence=min(
                            0.95,
                            funding_source.source_reliability * funding_source.identity_confidence,
                        ),
                        raw_payload={
                            "name": candidate.company_name,
                            "amount": funding.amount,
                            "round": funding.round,
                            "claim_kind": funding.claim_kind.value,
                            "funding_status": funding.status.value,
                            "round_type": funding.round_type,
                            "investors": funding.investors,
                            "lead_investor": funding.lead_investor,
                            "investor_relationships": {
                                signal.investor: signal.relationship.value
                                for signal in candidate.profile.investor_signals
                                if signal.source_url == funding.source_url
                            },
                            "investor_partners": {
                                signal.investor: signal.partner
                                for signal in candidate.profile.investor_signals
                                if signal.source_url == funding.source_url and signal.partner
                            },
                            "investment_attention_pct": (
                                candidate.profile.investment_attention_pct
                            ),
                        },
                        metadata={
                            **common_metadata,
                            "batch_confidence_unchanged_by_funding": True,
                            "funding_observation_id": funding_source.observation_id,
                        },
                    )
                )
        return person_source_id, company_source_id, event_bus.publish_batch(events)
