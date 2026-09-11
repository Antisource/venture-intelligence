"""Typed M8 latest-batch discovery API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from venture_intelligence.batch_discovery.factory import build_batch_discovery_service
from venture_intelligence.batch_discovery.models import (
    BatchCandidateDetailResponse,
    BatchDiscoveryContractResponse,
    BatchDiscoveryListResponse,
    BatchDiscoveryRequest,
    BatchDiscoveryRun,
    BatchDiscoverySourceCapability,
    CandidateHypothesis,
    DiscoveryStage,
    ManualBatchObservationRequest,
    SourceAccessMode,
    SourceTaxonomyRole,
)
from venture_intelligence.batch_discovery.repository import BatchDiscoveryRepository
from venture_intelligence.batch_discovery.service import BatchDiscoveryService
from venture_intelligence.batch_discovery.tools import TOOL_BENCHMARKS
from venture_intelligence.config import settings
from venture_intelligence.operations.cycle import IntelligenceCycleService
from venture_intelligence.programs import get_program_registry
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.storage.db import Database

router = APIRouter(prefix="/api/v1/batch-discovery", tags=["batch-discovery"])

SOURCE_CAPABILITIES = [
    BatchDiscoverySourceCapability(
        source_id="public-search-index",
        display_name="DuckDuckGo, Bing, Yahoo, and Brave public search",
        taxonomy_roles=[SourceTaxonomyRole.DISCOVERY],
        access_modes=[SourceAccessMode.PUBLIC_SEARCH],
        seeds_unknown_discovery=True,
        notes=(
            "Bounded provider fallback finds unknown candidates and indexed public "
            "social references; providers are acquisition paths, not evidence origins."
        ),
    ),
    BatchDiscoverySourceCapability(
        source_id="techcrunch",
        display_name="TechCrunch public RSS",
        taxonomy_roles=[
            SourceTaxonomyRole.DISCOVERY,
            SourceTaxonomyRole.FUNDING,
            SourceTaxonomyRole.MARKET_SIGNAL,
            SourceTaxonomyRole.TEMPORAL_NEWS,
        ],
        access_modes=[SourceAccessMode.PUBLIC_DIRECT],
        seeds_unknown_discovery=True,
        notes="Direct public feed items retain article publication and acquisition provenance.",
    ),
    BatchDiscoverySourceCapability(
        source_id="newsletter-watchlist",
        display_name="Configured public newsletter and Substack feeds",
        taxonomy_roles=[
            SourceTaxonomyRole.DISCOVERY,
            SourceTaxonomyRole.FUNDING,
            SourceTaxonomyRole.INVESTOR_SIGNAL,
            SourceTaxonomyRole.TEMPORAL_NEWS,
        ],
        access_modes=[SourceAccessMode.PUBLIC_DIRECT],
        seeds_unknown_discovery=True,
        notes="Small transparent YAML watchlist with incremental feed state.",
    ),
    BatchDiscoverySourceCapability(
        source_id="vc-watchlist",
        display_name="Configured public VC and partner pages",
        taxonomy_roles=[
            SourceTaxonomyRole.DISCOVERY,
            SourceTaxonomyRole.FUNDING,
            SourceTaxonomyRole.INVESTOR_SIGNAL,
            SourceTaxonomyRole.TEMPORAL_NEWS,
        ],
        access_modes=[SourceAccessMode.PUBLIC_DIRECT],
        seeds_unknown_discovery=True,
        notes="Public pages only; content hashes provide bounded change detection.",
    ),
    BatchDiscoverySourceCapability(
        source_id="public-program-announcements",
        display_name="Public program announcements and indexed references",
        taxonomy_roles=[
            SourceTaxonomyRole.DISCOVERY,
            SourceTaxonomyRole.VERIFICATION,
        ],
        access_modes=[
            SourceAccessMode.PUBLIC_SEARCH,
            SourceAccessMode.INDEXED_PUBLIC_REFERENCE,
        ],
        seeds_unknown_discovery=True,
        notes="Uses public indexes; direct restricted-social automation remains disabled.",
    ),
    BatchDiscoverySourceCapability(
        source_id="official-program-directory",
        display_name="Official accelerator/program directory",
        taxonomy_roles=[SourceTaxonomyRole.VERIFICATION],
        access_modes=[SourceAccessMode.PUBLIC_DIRECT],
        seeds_unknown_discovery=False,
        notes="Verification and fallback only; directory rows never seed this workflow.",
    ),
    BatchDiscoverySourceCapability(
        source_id="bounded-public-verifier",
        display_name="Bounded public-page verifier",
        taxonomy_roles=[
            SourceTaxonomyRole.IDENTITY,
            SourceTaxonomyRole.VERIFICATION,
        ],
        access_modes=[SourceAccessMode.PUBLIC_DIRECT],
        seeds_unknown_discovery=False,
        notes="Policy-checked public HTTP only, with shared budgets and circuit protection.",
    ),
    BatchDiscoverySourceCapability(
        source_id="technical-public-sources",
        display_name="GitHub, Hugging Face, PyPI/npm, arXiv, and ORCID",
        taxonomy_roles=[
            SourceTaxonomyRole.IDENTITY,
            SourceTaxonomyRole.ENRICHMENT,
        ],
        access_modes=[SourceAccessMode.PUBLIC_SEARCH, SourceAccessMode.PUBLIC_DIRECT],
        seeds_unknown_discovery=False,
        notes="Enriches an already discovered hypothesis; never proves program membership.",
    ),
    BatchDiscoverySourceCapability(
        source_id="wayback",
        display_name="Wayback historical evidence",
        taxonomy_roles=[SourceTaxonomyRole.HISTORICAL],
        access_modes=[SourceAccessMode.PUBLIC_SEARCH, SourceAccessMode.PUBLIC_DIRECT],
        seeds_unknown_discovery=False,
        notes="Historical evidence remains temporally labelled and cannot imply current state.",
    ),
    BatchDiscoverySourceCapability(
        source_id="manual-username-tool-export",
        display_name="Maigret/Sherlock analyst-supplied JSON",
        taxonomy_roles=[
            SourceTaxonomyRole.IDENTITY,
            SourceTaxonomyRole.ENRICHMENT,
        ],
        access_modes=[SourceAccessMode.MANUAL_OFFLINE_IMPORT],
        seeds_unknown_discovery=False,
        notes="Parses offline exports only; Venture Intelligence never runs their network modes.",
    ),
]


def _repository() -> BatchDiscoveryRepository:
    return BatchDiscoveryRepository(Database(settings.db_path))


def _service(program_id: str | None) -> BatchDiscoveryService:
    try:
        return build_batch_discovery_service(
            program_id,
            database=Database(settings.db_path),
            registry=get_program_registry(),
        )
    except LookupError as error:
        raise HTTPException(404, f"Unknown active program id: {program_id}") from error


@router.get("/contract", response_model=BatchDiscoveryContractResponse)
async def discovery_contract() -> BatchDiscoveryContractResponse:
    """Expose promotion, taxonomy, and external-tool safety invariants."""
    return BatchDiscoveryContractResponse(
        stages=list(DiscoveryStage),
        source_taxonomy=[
            SourceTaxonomyRole.DISCOVERY,
            SourceTaxonomyRole.IDENTITY,
            SourceTaxonomyRole.VERIFICATION,
            SourceTaxonomyRole.ENRICHMENT,
            SourceTaxonomyRole.HISTORICAL,
        ],
        source_capabilities=SOURCE_CAPABILITIES,
        tool_benchmarks=TOOL_BENCHMARKS,
    )


@router.get("", response_model=BatchDiscoveryListResponse)
async def latest_batch_candidates(
    program_id: str | None = Query(default=None, min_length=1),
    batch_id: str | None = Query(default=None, min_length=1),
    include_rejected: bool = True,
) -> BatchDiscoveryListResponse:
    try:
        return BatchDiscoveryService(
            _repository(),
            [],
            registry=get_program_registry(),
        ).list_candidates(
            program_id,
            batch_id=batch_id,
            include_rejected=include_rejected,
        )
    except (LookupError, ValueError) as error:
        raise HTTPException(404, str(error)) from error


@router.post("/runs", response_model=BatchDiscoveryRun)
async def run_latest_batch_discovery(request: BatchDiscoveryRequest) -> BatchDiscoveryRun:
    service = _service(request.program_id)
    database = service.repository.database
    try:
        result = await service.run(request, event_bus=EventBus(database))
        await IntelligenceCycleService(database=database).process_canonical_events()
        return result
    finally:
        await service.close()


@router.post("/manual-observations", response_model=CandidateHypothesis)
async def add_manual_observation(
    request: ManualBatchObservationRequest,
) -> CandidateHypothesis:
    service = BatchDiscoveryService(
        _repository(),
        [],
        registry=get_program_registry(),
    )
    try:
        result = service.ingest_manual(
            request,
            event_bus=EventBus(service.repository.database),
        )
        await IntelligenceCycleService(
            database=service.repository.database
        ).process_canonical_events()
        return result
    except (LookupError, ValueError) as error:
        raise HTTPException(422, str(error)) from error


@router.get("/{hypothesis_id}", response_model=BatchCandidateDetailResponse)
async def batch_candidate(hypothesis_id: str) -> BatchCandidateDetailResponse:
    service = BatchDiscoveryService(_repository(), [], registry=get_program_registry())
    detail = service.candidate_detail(hypothesis_id)
    if detail is None:
        raise HTTPException(404, "Batch candidate hypothesis not found")
    return detail
