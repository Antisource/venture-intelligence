"""Bounded free/public research orchestration.

The agent searches only a public HTML search endpoint, records search snippets
as lower-confidence evidence, and never follows a result into an authenticated
destination. Results are JSON-native Pydantic models and can be published as
typed canonical events for the existing pipeline.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Any, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from venture_intelligence.config import Settings, settings
from venture_intelligence.knowledge.compiler import KnowledgeCompiler
from venture_intelligence.observability.metrics import metrics
from venture_intelligence.pipeline.entity_integrity import is_generic_label
from venture_intelligence.research.query import QueryGenerator
from venture_intelligence.research.request_budget import RequestBudgetTracker
from venture_intelligence.research.sources import (
    SearchProvider,
    SearchResponse,
    SearchResult,
)
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    CanonicalEvent,
    EntityType,
    EventType,
    EvidenceAttributionStatus,
    ResearchBudget,
    ResearchDiscoveryMethod,
    ResearchEntity,
    ResearchEvidence,
    ResearchHypothesis,
    ResearchIdentityStatus,
    ResearchProgramStatus,
    ResearchProviderSkip,
    ResearchQuery,
    ResearchRejectedResult,
    ResearchRun,
    ResearchSource,
    ResearchSourceStatus,
    ResearchTarget,
    ResearchTargetType,
    Source,
    SourceOperationStatus,
    TemporalPrecision,
)

_OFFICIAL_DOMAINS = {
    "github.com",
    "arxiv.org",
    "news.ycombinator.com",
    "ycombinator.com",
    "a16z.com",
    "speedrun.a16z.com",
}
_AUTH_DESTINATIONS = {
    "linkedin.com",
    "x.com",
    "instagram.com",
    "threads.net",
    "producthunt.com",
    "medium.com",
}


class ResearchCache:
    """Small process-local TTL cache; it prevents duplicate public searches."""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[float, SearchResponse]] = {}

    def get(self, query: str) -> SearchResponse | None:
        key = _query_key(query)
        item = self._entries.get(key)
        if item is None:
            return None
        created, response = item
        if time.monotonic() - created > self.ttl_seconds:
            self._entries.pop(key, None)
            return None
        return response.model_copy(
            update={
                "attempts": 0,
                "retry_requests": 0,
                "redirect_requests": 0,
                "from_cache": True,
            }
        )

    def put(self, query: str, response: SearchResponse) -> None:
        self._entries[_query_key(query)] = (time.monotonic(), response)


def _query_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "target"


def _confidence_label(value: float, domains: int) -> str:
    if value >= 0.85 and domains >= 3:
        return "high-confidence"
    if domains >= 2:
        return "corroborated"
    if value >= 0.60:
        return "probable"
    return "discovered"


def _source_status_for_domain(
    domain: str, discovery_method: str = "search_engine"
) -> tuple[ResearchSourceStatus, str, int]:
    if discovery_method == "public_api":
        return ResearchSourceStatus.AVAILABLE, "public_api", 1
    if domain == "web.archive.org":
        return ResearchSourceStatus.AVAILABLE, "direct_public", 2
    if domain in _AUTH_DESTINATIONS or any(
        domain.endswith(f".{item}") for item in _AUTH_DESTINATIONS
    ):
        return ResearchSourceStatus.SEARCH_DISCOVERABLE, "search_discovery", 3
    if domain in _OFFICIAL_DOMAINS or any(
        domain.endswith(f".{item}") for item in _OFFICIAL_DOMAINS
    ):
        return ResearchSourceStatus.SEARCH_DISCOVERABLE, "search_discovery", 1
    return ResearchSourceStatus.SEARCH_DISCOVERABLE, "search_discovery", 2


def _result_confidence(result: SearchResult, query: ResearchQuery) -> float:
    base = 0.48
    if result.source_domain in _OFFICIAL_DOMAINS:
        base = 0.62
    if result.discovery_method == "public_api":
        base = max(base, 0.70)
    if query.family == "site":
        base += 0.04
    if query.family in {"founder_follow_up", "github_follow_up"}:
        base += 0.03
    return min(0.78, base)


def _result_relevance(
    result: SearchResult, target: ResearchTarget, query: ResearchQuery
) -> tuple[bool, str]:
    """Reject retrieved records that do not actually support the target query."""
    del query
    text = f"{result.title} {result.snippet}".casefold()
    phrase = " ".join(target.name.casefold().split())
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", phrase)
        if len(token) > 2 and token not in {"company", "companies", "startup", "founder"}
    }
    result_tokens = set(re.findall(r"[a-z0-9]+", text))
    declared = {item.casefold().removeprefix("www.").split("/", 1)[0] for item in target.domains}
    result_domain = result.source_domain.casefold().removeprefix("www.")
    if declared and result_domain in declared:
        return True, "declared_target_domain"
    if phrase and phrase in text:
        return True, "exact_target_phrase"
    if not tokens:
        return False, "target_has_no_discriminating_terms"
    overlap = len(tokens & result_tokens)
    required = max(1, (len(tokens) + 1) // 2)
    if overlap < required:
        return False, "target_query_mismatch"
    return True, "target_terms_matched"


def _identity_attribution(
    evidence: ResearchEvidence,
    target: ResearchTarget,
) -> tuple[EvidenceAttributionStatus, str]:
    """Classify one result; a run-level anchor never blesses sibling results."""
    if evidence.metadata.get("relevant") is False:
        return EvidenceAttributionStatus.UNBOUND, "query_result_not_relevant"
    stance = str(evidence.metadata.get("stance") or "").casefold()
    if stance in {"contradicts", "contradicted", "negative"}:
        return EvidenceAttributionStatus.CONTRADICTORY, "explicit_contradictory_claim"

    text = " ".join(f"{evidence.title} {evidence.snippet}".casefold().split())
    target_name = " ".join(target.name.casefold().split())
    exact_name = bool(target_name and target_name in text)
    url = (evidence.canonical_url or evidence.url).casefold()
    domain = evidence.source_domain.casefold().removeprefix("www.")
    declared = {
        item.casefold().removeprefix("www.").split("/", 1)[0]
        for item in target.domains
        if item
    }
    first_party = any(domain == item or domain.endswith(f".{item}") for item in declared)
    founder_context = bool(
        re.search(r"\b(?:co[- ]?)?founder\b|\bchief executive\b|\bceo\b", text, re.I)
    )
    company_page = "/companies/" in url or "/company/" in url
    official_program_page = domain in _OFFICIAL_DOMAINS and company_page

    if target.type == ResearchTargetType.PERSON:
        if exact_name and "/in/" in url and founder_context:
            return EvidenceAttributionStatus.ATTACHED, "indexed_profile_with_founder_context"
        if exact_name and official_program_page and founder_context:
            return EvidenceAttributionStatus.ATTACHED, "official_program_founder_record"
        if exact_name and first_party and founder_context:
            return EvidenceAttributionStatus.ATTACHED, "declared_domain_founder_record"
        return EvidenceAttributionStatus.UNBOUND, "name_match_without_identity_anchor"

    if target.type in {ResearchTargetType.COMPANY, ResearchTargetType.PRODUCT}:
        if exact_name and company_page:
            return EvidenceAttributionStatus.ATTACHED, "explicit_company_profile"
        if first_party and (exact_name or domain in declared):
            return EvidenceAttributionStatus.ATTACHED, "declared_company_domain"
        return EvidenceAttributionStatus.UNBOUND, "name_match_without_company_anchor"
    return EvidenceAttributionStatus.UNBOUND, "target_type_has_no_identity_semantics"


def _evidence_origin(evidence: ResearchEvidence) -> str:
    raw = (
        evidence.evidence_origin_url
        or evidence.provenance.get("evidence_origin")
        or evidence.metadata.get("evidence_origin")
        or evidence.canonical_url
        or evidence.url
    )
    return _canonical_url(str(raw))


def _merge_acquisition_paths(existing: ResearchEvidence, incoming: ResearchEvidence) -> None:
    providers = list(
        dict.fromkeys([*existing.acquisition_providers, *incoming.acquisition_providers])
    )
    existing.acquisition_providers = providers
    existing.metadata["acquisition_providers"] = providers
    existing.provenance["acquisition_providers"] = providers


def _canonical_url(value: str) -> str:
    """Strip common tracking parameters while retaining the raw URL in provenance."""
    parts = urlsplit(value)
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith(("utm_", "trk", "fbclid", "gclid"))
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.casefold(), (parts.netloc or "").casefold(), path, urlencode(query), "")
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _discovery_method(value: str) -> ResearchDiscoveryMethod:
    return {
        "public_api": ResearchDiscoveryMethod.PUBLIC_API,
        "rss": ResearchDiscoveryMethod.RSS,
        "direct_public": ResearchDiscoveryMethod.DIRECT_PUBLIC,
    }.get(value, ResearchDiscoveryMethod.SEARCH_ENGINE)


class ResearchAgent:
    """Run deterministic, bounded research using one free/public provider."""

    def __init__(
        self,
        provider: SearchProvider | Sequence[SearchProvider],
        query_generator: QueryGenerator | None = None,
        runtime_settings: Settings = settings,
        cache: ResearchCache | None = None,
        knowledge_compiler: KnowledgeCompiler | None = None,
    ) -> None:
        self.providers = list(provider) if isinstance(provider, (list, tuple)) else [provider]
        self.provider = self.providers[0]
        self.query_generator = query_generator or QueryGenerator()
        self.settings = runtime_settings
        self.cache = cache or ResearchCache(runtime_settings.research_cache_ttl_seconds)
        self.knowledge_compiler = knowledge_compiler

    def default_budget(self) -> ResearchBudget:
        return ResearchBudget(
            max_queries=self.settings.research_max_queries,
            max_depth=self.settings.research_max_depth,
            max_results_per_query=self.settings.research_max_results_per_query,
            max_runtime_seconds=self.settings.research_max_runtime_seconds,
            max_total_requests=self.settings.research_max_total_requests,
            max_requests_per_provider=self.settings.research_max_requests_per_provider,
            max_retry_requests=self.settings.research_max_retry_requests,
            max_requests_per_domain=self.settings.research_max_requests_per_domain,
        )

    async def run(
        self,
        target: ResearchTarget,
        budget: ResearchBudget | None = None,
        event_bus: EventBus | None = None,
    ) -> ResearchRun:
        started = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        selected_budget = budget or self.default_budget()
        run = ResearchRun(target=target, budget=selected_budget, started_at=started)
        request_budget = RequestBudgetTracker(selected_budget)
        for provider in self.providers:
            setter = getattr(provider, "set_request_budget", None)
            if setter is not None:
                setter(request_budget)
        queue = list(self.query_generator.generate(target))
        seen_queries: set[str] = set()
        seen_evidence: dict[str, ResearchEvidence] = {}
        source_index: dict[str, int] = {}
        query_count = 0
        stop_reason = "budget_exhausted"

        while queue and query_count < selected_budget.max_queries:
            if time.monotonic() - started_monotonic >= selected_budget.max_runtime_seconds:
                stop_reason = "runtime_budget_exhausted"
                break
            query = queue.pop(0)
            query_key = _query_key(query.query)
            if query_key in seen_queries or query.depth > selected_budget.max_depth:
                continue
            seen_queries.add(query_key)
            run.queries.append(query)
            query_count += 1
            responses = await self._search_with_budget(
                query,
                selected_budget.max_results_per_query,
                selected_budget.max_runtime_seconds - (time.monotonic() - started_monotonic),
                run,
            )
            query_succeeded = False
            for response in responses:
                domain = response.provider_domain or getattr(
                    self.providers[0], "domain", "public-search"
                )
                self._record_provider_source(response, source_index, run, domain)
                if response.status == ResearchSourceStatus.BUDGET_EXHAUSTED:
                    stop_reason = "request_budget_exhausted"
                    if not response.results:
                        continue
                elif response.status != ResearchSourceStatus.AVAILABLE:
                    run.queries_failed += 1
                    if response.error == "runtime budget exhausted":
                        stop_reason = "runtime_budget_exhausted"
                    continue
                query_succeeded = True
                for result in response.results:
                    run.retrieved_results += 1
                    relevant, reason = _result_relevance(result, target, query)
                    if not relevant:
                        run.rejected_results.append(
                            ResearchRejectedResult(
                                url=result.url,
                                provider_domain=domain,
                                query=query.query,
                                reason=reason,
                            )
                        )
                        continue
                    run.relevant_results += 1
                    evidence = self._evidence(target, query, result, len(run.evidence))
                    evidence.metadata.update({"relevant": True, "relevance_reason": reason})
                    evidence.attribution_status, evidence.attribution_reason = (
                        _identity_attribution(evidence, target)
                    )
                    # Acquisition paths are not independent evidence. Collapse
                    # the same canonical origin even when providers emit
                    # different snippets or tracking URLs.
                    evidence_key = _evidence_origin(evidence)
                    if evidence_key in seen_evidence:
                        _merge_acquisition_paths(seen_evidence[evidence_key], evidence)
                        continue
                    seen_evidence[evidence_key] = evidence
                    run.evidence.append(evidence)
                    for follow_up in self.query_generator.follow_ups(
                        target, evidence, query.depth + 1
                    ):
                        if _query_key(follow_up.query) not in seen_queries:
                            queue.append(follow_up)
                            run.followups_executed += 1
            if query_succeeded:
                run.queries_succeeded += 1
            if stop_reason in {"runtime_budget_exhausted", "request_budget_exhausted"}:
                break

        if request_budget.stop_reason is not None or stop_reason == "request_budget_exhausted":
            stop_reason = "request_budget_exhausted"
        elif query_count >= selected_budget.max_queries and queue:
            stop_reason = "max_queries_reached"
        elif not queue and stop_reason == "budget_exhausted":
            stop_reason = (
                "completed"
                if run.relevant_results
                else ("no_relevant_evidence" if run.retrieved_results else "no_results")
            )
        if not run.evidence and stop_reason == "budget_exhausted":
            stop_reason = (
                "provider_unavailable"
                if run.sources
                and all(item.status != ResearchSourceStatus.AVAILABLE for item in run.sources)
                else "no_results"
            )
        usable_domains = {
            item.source_domain
            for item in run.evidence
            if item.source_domain and item.metadata.get("relevant") is not False
        }
        if run.relevant_results and len(usable_domains) >= 2:
            stop_reason = "evidence_sufficient" if stop_reason == "completed" else stop_reason

        self._complete_run(run, source_index, stop_reason)
        if self.knowledge_compiler is not None:
            self.knowledge_compiler.compile_run(run)
        run.sources_checked = len(run.sources)
        run.budget_remaining = max(0, selected_budget.max_queries - query_count)
        request_snapshot = request_budget.snapshot()
        run.requests_used = int(request_snapshot["requests_used"])
        run.retry_requests_used = int(request_snapshot["retry_requests_used"])
        run.redirect_requests_used = int(request_snapshot["redirect_requests_used"])
        run.retries_used = run.retry_requests_used
        run.request_budget_remaining = int(request_snapshot["request_budget_remaining"])
        run.provider_request_counts = dict(request_snapshot["provider_request_counts"])
        run.domain_request_counts = dict(request_snapshot["domain_request_counts"])
        if event_bus is not None:
            emitted = self._publish_events(run, event_bus)
            run.events_emitted = emitted
        return run

    async def _search_with_budget(
        self,
        query: ResearchQuery,
        max_results: int,
        remaining_seconds: float,
        run: ResearchRun,
    ) -> list[SearchResponse]:
        cached_responses: list[SearchResponse] = []
        missing: list[SearchProvider] = []
        for provider in self.providers:
            domain = getattr(provider, "domain", "public-search")
            policy = getattr(provider, "policy", None)
            accepted = getattr(policy, "accepted_query_intents", None)
            if accepted is not None and query.intent not in accepted:
                provider_id = getattr(policy, "provider_id", domain)
                run.provider_skips.append(
                    ResearchProviderSkip(
                        provider_id=provider_id,
                        query=query.query,
                        intent=query.intent,
                        reason="query_intent_not_supported",
                    )
                )
                continue
            cached = self.cache.get(f"{domain}|{query.query}")
            if cached is None:
                missing.append(provider)
            else:
                cached_responses.append(cached)
        if not missing:
            return cached_responses
        if remaining_seconds <= 0:
            return cached_responses + [
                SearchResponse(
                    query=query.query,
                    status=ResearchSourceStatus.UNAVAILABLE,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    error="runtime budget exhausted",
                    provider_domain=getattr(provider, "domain", "public-search"),
                )
                for provider in missing
            ]

        async def call(provider: SearchProvider) -> SearchResponse:
            domain = getattr(provider, "domain", "public-search")
            try:
                response = await asyncio.wait_for(
                    provider.search(query.query, max_results), timeout=remaining_seconds
                )
            except asyncio.TimeoutError:
                response = SearchResponse(
                    query=query.query,
                    status=ResearchSourceStatus.UNAVAILABLE,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    error="runtime budget exhausted",
                )
            except Exception as error:
                response = SearchResponse(
                    query=query,
                    status=ResearchSourceStatus.FAILED,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    error=str(error)[:500],
                )
            response = response.model_copy(update={"provider_domain": domain})
            if (
                response.status == ResearchSourceStatus.AVAILABLE or bool(response.results)
            ) and not response.from_cache:
                metrics.record_evidence_outcome(domain, bool(response.results))
            if response.status == ResearchSourceStatus.AVAILABLE:
                self.cache.put(f"{domain}|{query.query}", response)
            return response

        return cached_responses + list(
            await asyncio.gather(*(call(provider) for provider in missing))
        )

    @staticmethod
    def _record_provider_source(
        response: SearchResponse,
        source_index: dict[str, int],
        run: ResearchRun,
        domain: str,
    ) -> None:
        existing = source_index.get(domain)
        if existing is None:
            source_index[domain] = len(run.sources)
            access_mode = "public_api" if domain == "arxiv.org" else "search_engine"
            run.sources.append(
                ResearchSource(
                    domain=domain,
                    status=response.status,
                    access_mode=access_mode,
                    source_tier=1 if access_mode == "public_api" else 3,
                    http_status=response.http_status,
                    queries_attempted=1,
                    retries_used=response.retry_requests,
                    notes=response.error,
                    normalized_status=response.operation_status,
                )
            )
        else:
            source = run.sources[existing]
            source.queries_attempted += 1
            source.retries_used += response.retry_requests
            if (
                source.status != ResearchSourceStatus.AVAILABLE
                and response.status == ResearchSourceStatus.AVAILABLE
            ):
                source.status = response.status
            if response.results:
                source.normalized_status = (
                    SourceOperationStatus.SUCCESS
                    if source.normalized_status is SourceOperationStatus.EMPTY
                    else SourceOperationStatus.PARTIAL
                )

    def _evidence(
        self,
        target: ResearchTarget,
        query: ResearchQuery,
        result: SearchResult,
        ordinal: int,
    ) -> ResearchEvidence:
        status, access_mode, tier = _source_status_for_domain(
            result.source_domain, result.discovery_method
        )
        confidence = _result_confidence(result, query)
        origin = _canonical_url(
            str(
                result.provenance.get("evidence_origin")
                or result.metadata.get("evidence_origin")
                or result.url
            )
        )
        providers = list(
            dict.fromkeys(
                [
                    *(
                        result.metadata.get("acquisition_providers", [])
                        if isinstance(result.metadata.get("acquisition_providers"), list)
                        else []
                    ),
                    str(
                        result.provenance.get("acquisition_provider")
                        or result.metadata.get("acquisition_provider")
                        or ""
                    ),
                ]
            )
        )
        providers = [item for item in providers if item]
        return ResearchEvidence(
            evidence_id=f"research-evidence-{ordinal + 1}",
            url=result.url,
            canonical_url=_canonical_url(result.url),
            title=result.title,
            snippet=result.snippet,
            source_domain=result.source_domain,
            retrieved_at=datetime.now(timezone.utc),
            published_at=_parse_datetime(result.published_at),
            discovery_method=_discovery_method(result.discovery_method),
            query=query.query,
            confidence=confidence,
            confidence_label="discovered",
            target_name=target.name,
            entity_type=target.type,
            direct_public=result.direct_public,
            source_status=status,
            depth=query.depth,
            metadata={"access_mode": access_mode, "source_tier": tier, **result.metadata},
            temporal_status="historical"
            if result.source_domain == "web.archive.org"
            else "current",
            provenance=result.provenance,
            evidence_origin_url=origin,
            acquisition_providers=providers,
        )

    def _complete_run(
        self, run: ResearchRun, source_index: dict[str, int], stop_reason: str
    ) -> None:
        for evidence in run.evidence:
            domain = evidence.source_domain
            if domain not in source_index:
                status, access_mode, tier = _source_status_for_domain(domain)
                source_index[domain] = len(run.sources)
                run.sources.append(
                    ResearchSource(
                        domain=domain,
                        status=status,
                        access_mode=access_mode,
                        source_tier=tier,
                        normalized_status=SourceOperationStatus.SUCCESS,
                    )
                )
        usable = [item for item in run.evidence if item.metadata.get("relevant") is not False]
        attached = [
            item
            for item in usable
            if item.attribution_status is EvidenceAttributionStatus.ATTACHED
        ]
        contradictory = [
            item
            for item in usable
            if item.attribution_status is EvidenceAttributionStatus.CONTRADICTORY
        ]
        unbound = [
            item
            for item in usable
            if item.attribution_status is EvidenceAttributionStatus.UNBOUND
        ]
        origin_domains = {
            urlsplit(_evidence_origin(item)).hostname or ""
            for item in attached
            if _evidence_origin(item)
        }
        origin_domains.discard("")
        for evidence in attached:
            evidence.confidence = min(
                0.95,
                evidence.confidence
                + min(0.16, 0.08 * max(0, len(origin_domains) - 1)),
            )
            evidence.confidence_label = _confidence_label(
                evidence.confidence, len(origin_domains)
            )
        for evidence in unbound:
            evidence.confidence_label = "unbound"
        for evidence in contradictory:
            evidence.confidence_label = "contradictory"
        run.confidence = max((item.confidence for item in attached), default=0.0)
        run.confidence_label = (
            _confidence_label(run.confidence, len(origin_domains))
            if attached
            else ("ambiguous" if unbound else "insufficient_evidence")
        )
        run.independent_evidence_origins = len(origin_domains)
        run.attached_evidence_count = len(attached)
        run.unbound_evidence_count = len(unbound)
        run.contradictory_evidence_count = len(contradictory)
        run.identity_status = (
            ResearchIdentityStatus.CORROBORATED
            if len(origin_domains) >= 2
            else ResearchIdentityStatus.PROVISIONAL
            if attached
            else ResearchIdentityStatus.AMBIGUOUS
            if unbound
            else ResearchIdentityStatus.UNRESOLVED
        )
        run.program_status = (
            ResearchProgramStatus.CONTRADICTED
            if run.target.program_id and contradictory and not attached
            else ResearchProgramStatus.VERIFIED
            if run.target.program_id
            and any(
                item.attribution_reason == "official_program_founder_record"
                for item in attached
            )
            else ResearchProgramStatus.HYPOTHESIS
            if run.target.program_id
            else ResearchProgramStatus.UNKNOWN
        )
        # Evidence is retained for investigation, but it is not an entity
        # until an identity-bearing page has been observed.  In particular,
        # generic targets and repository/paper keyword matches must never
        # enter the entity/event pipeline merely because a search returned.
        run.entities = []
        if (
            attached
            and not is_generic_label(run.target.name)
            and len(origin_domains) >= 2
        ):
            run.entities = [
                ResearchEntity(
                    name=run.target.name,
                    entity_type=run.target.type,
                    confidence=run.confidence,
                    evidence_ids=[item.evidence_id for item in attached],
                    source_urls=list(dict.fromkeys(item.url for item in attached)),
                )
            ]
        elif usable:
            run.next_actions.append(
                "Candidate retained as evidence only: no explicit identity-bearing "
                "public page was found."
            )
        if attached:
            run.hypotheses.append(
                ResearchHypothesis(
                    statement=(
                        f"Public sources provide {run.confidence_label} evidence for "
                        f"{run.target.name}."
                    ),
                    status=run.confidence_label,
                    confidence=run.confidence,
                    evidence_ids=[item.evidence_id for item in attached],
                )
            )
            run.next_actions.append(
                "Triangulate the strongest claims with an independent public source."
            )
        else:
            run.next_actions.append(
                "Review structured source statuses and retry later; no public evidence "
                "was asserted."
            )
        if any(source.status != ResearchSourceStatus.AVAILABLE for source in run.sources):
            run.next_actions.append(
                "Use available RSS, public API, or site-restricted discovery fallbacks "
                "for unavailable sources."
            )
        run.stop_reason = stop_reason
        run.finished_at = datetime.now(timezone.utc)

    def _publish_events(self, run: ResearchRun, event_bus: EventBus) -> int:
        entity_type = {
            ResearchTargetType.COMPANY: EntityType.COMPANY,
            ResearchTargetType.PERSON: EntityType.PERSON,
            ResearchTargetType.PRODUCT: EntityType.COMPANY,
        }.get(run.target.type)
        if entity_type is None:
            return 0
        if is_generic_label(run.target.name):
            run.next_actions.append(
                "Candidate not promoted: target is a generic search/platform label."
            )
            return 0
        usable = [
            item
            for item in run.evidence
            if item.metadata.get("relevant") is not False
            and item.attribution_status is EvidenceAttributionStatus.ATTACHED
        ]
        supporting_domains = {
            urlsplit(_evidence_origin(item)).hostname or "" for item in usable
        }
        supporting_domains.discard("")
        if len(supporting_domains) < 2:
            run.next_actions.append(
                "Candidate not promoted: identity requires independent public-source corroboration."
            )
            return 0
        entity_id = f"research__{_slug(run.target.name)}"
        emitted = 0
        for evidence in usable:
            event = CanonicalEvent(
                event_type=EventType.RESEARCH_DISCOVERED,
                entity_type=entity_type,
                entity_id=entity_id,
                source=Source.RESEARCH_AGENT,
                source_url=evidence.url,
                occurred_at=evidence.published_at or evidence.retrieved_at,
                occurred_at_precision=(
                    TemporalPrecision.EXACT
                    if evidence.published_at is not None
                    else TemporalPrecision.OBSERVED_FALLBACK
                ),
                observed_at=evidence.retrieved_at,
                ingested_at=evidence.retrieved_at,
                confidence=evidence.confidence,
                raw_payload={
                    "name": run.target.name,
                    "profile_url": (
                        evidence.canonical_url or evidence.url
                        if entity_type is EntityType.PERSON
                        and "/in/" in (evidence.canonical_url or evidence.url).casefold()
                        else None
                    ),
                    "title": evidence.title,
                    "snippet": evidence.snippet,
                    "research_evidence": evidence.model_dump(mode="json"),
                },
                metadata={
                    "research_run_id": run.run_id,
                    "query": evidence.query,
                    "discovery_method": evidence.discovery_method.value,
                    "confidence_label": evidence.confidence_label,
                    "source_domain": evidence.source_domain,
                    "identity_validated": True,
                    "identity_evidence": True,
                    "identity_evidence_urls": [item.url for item in usable],
                    "attribution_status": evidence.attribution_status.value,
                    "attribution_reason": evidence.attribution_reason,
                    "evidence_origin": _evidence_origin(evidence),
                    "acquisition_providers": evidence.acquisition_providers,
                    "supporting_source_count": len(supporting_domains),
                    "evidence_tier": "corroborated_public_source",
                    "temporal_status": evidence.temporal_status,
                    "provenance": evidence.provenance,
                },
            )
            if event_bus.publish(event):
                emitted += 1
        return emitted

    async def close(self) -> None:
        closed: set[int] = set()
        for provider in self.providers:
            if id(provider) in closed:
                continue
            closed.add(id(provider))
            close = getattr(provider, "close", None)
            if close is not None:
                result: Any = close()
                if hasattr(result, "__await__"):
                    await result
