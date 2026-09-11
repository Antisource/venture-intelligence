"""Versioned intelligence API, independent of the dashboard frontend."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from venture_intelligence.config import settings
from venture_intelligence.connectors.factory import build_connectors
from venture_intelligence.decisions.models import (
    CRMActionResult,
    DecisionBoardResponse,
    DecisionEntityResponse,
)
from venture_intelligence.decisions.service import DecisionService
from venture_intelligence.evaluation.models import (
    EvaluationContractResponse,
    EvaluationListResponse,
    EvaluationRun,
)
from venture_intelligence.evaluation.service import EvaluationService
from venture_intelligence.graph.models import GraphSnapshotResponse
from venture_intelligence.graph.service import GraphService
from venture_intelligence.intelligence.models import (
    EntityIntelligenceResponse,
    RankingIntelligenceResponse,
)
from venture_intelligence.intelligence.service import IntelligenceService
from venture_intelligence.knowledge.compiler import KnowledgeCompiler
from venture_intelligence.operations.cycle import (
    CycleAlreadyRunning,
    IntelligenceCycleRecord,
    IntelligenceCycleService,
    OperationsState,
    OperationsStateStore,
    SchedulerState,
)
from venture_intelligence.operations.inventory import build_connector_inventory
from venture_intelligence.operations.models import (
    ConnectorInventoryItem,
    OperationalValidationContract,
    SourceQualityResponse,
)
from venture_intelligence.operations.readiness import SystemReadiness, system_readiness
from venture_intelligence.operations.store import SourceQualityReportStore
from venture_intelligence.pipeline.entity_integrity import is_generic_label, validate_candidate
from venture_intelligence.programs import ProgramConfig, get_program_registry
from venture_intelligence.replay.models import ReplayContractResponse
from venture_intelligence.replay.service import SUPPRESSED_REPLAY_SIDE_EFFECTS
from venture_intelligence.research.factory import build_research_agent
from venture_intelligence.research.provider_policy import (
    BUILTIN_PROVIDER_POLICIES,
    CONNECTOR_POLICIES,
    OPTIONAL_INTEGRATION_POLICIES,
    ProviderPolicy,
    SourceOperationStatus,
)
from venture_intelligence.research.watchlist import (
    IntelligenceWatchlist,
    SourceSettingsStore,
    SourceStateStore,
    WatchlistEntry,
)
from venture_intelligence.shared.bus import EventBus
from venture_intelligence.shared.models import (
    AlertLifecycleEvent,
    AlertLifecycleStatus,
    CanonicalEvent,
    CRMActionType,
    CRMStage,
    EntityType,
    EventType,
    GraphNodeType,
    RecommendationLifecycleEvent,
    RecommendationLifecycleStatus,
    ResearchBudget,
    ResearchRun,
    ResearchTarget,
    ResearchTargetType,
    Source,
)
from venture_intelligence.storage.db import Database

router = APIRouter(prefix="/api/v1", tags=["v1"])


class GatherRequest(BaseModel):
    entity_name: str = Field(min_length=1, max_length=200)
    program_id: str | None = None


class GatherSourceStatus(str, Enum):
    SUCCESS = "SUCCESS"
    SUCCESS_EMPTY = "SUCCESS_EMPTY"
    SKIPPED = "SKIPPED"
    CANDIDATE_REJECTED = "CANDIDATE_REJECTED"
    TIMEOUT = "TIMEOUT"
    FAILED = "FAILED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    BLOCKED = "BLOCKED"
    RATE_LIMITED = "RATE_LIMITED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class GatherSourceOutcome(BaseModel):
    source: str
    attempted: bool
    status: GatherSourceStatus
    events_found: int = 0
    events_accepted: int = 0
    events_rejected: int = 0
    error_category: str | None = None
    final_url: str | None = None
    http_status: int | None = None
    normalized_status: SourceOperationStatus | None = None

    @model_validator(mode="after")
    def _normalize_status(self) -> GatherSourceOutcome:
        if self.normalized_status is not None:
            return self
        self.normalized_status = {
            GatherSourceStatus.SUCCESS: SourceOperationStatus.SUCCESS,
            GatherSourceStatus.SUCCESS_EMPTY: SourceOperationStatus.EMPTY,
            GatherSourceStatus.SKIPPED: SourceOperationStatus.SKIPPED,
            GatherSourceStatus.CANDIDATE_REJECTED: SourceOperationStatus.CANDIDATE_REJECTED,
            GatherSourceStatus.TIMEOUT: SourceOperationStatus.TIMEOUT,
            GatherSourceStatus.FAILED: SourceOperationStatus.FAILED,
            GatherSourceStatus.AUTH_REQUIRED: SourceOperationStatus.AUTH_REQUIRED,
            GatherSourceStatus.BLOCKED: SourceOperationStatus.BLOCKED,
            GatherSourceStatus.RATE_LIMITED: SourceOperationStatus.RATE_LIMITED,
            GatherSourceStatus.TEMPORARILY_UNAVAILABLE: (
                SourceOperationStatus.TEMPORARILY_UNAVAILABLE
            ),
            GatherSourceStatus.UNAVAILABLE: SourceOperationStatus.UNAVAILABLE,
            GatherSourceStatus.BUDGET_EXHAUSTED: SourceOperationStatus.BUDGET_EXHAUSTED,
        }[self.status]
        return self


class GatherResponse(BaseModel):
    entity_name: str
    mode: str = "preview_corroboration"
    persisted_events: int = 0
    sources_considered: list[str]
    sources_attempted: list[str]
    outcomes: list[GatherSourceOutcome]
    events_found: int
    events_accepted: int
    events_rejected: int
    gathered_at: datetime


class ResearchRequest(BaseModel):
    entity_name: str = Field(min_length=1, max_length=200)
    entity_type: ResearchTargetType = ResearchTargetType.OTHER
    program_id: str | None = None
    aliases: list[str] = Field(default_factory=list, max_length=20)
    domains: list[str] = Field(default_factory=list, max_length=20)
    budget: ResearchBudget | None = None


class CycleRunRequest(BaseModel):
    program_ids: list[str] | None = None


class SchedulerControlRequest(BaseModel):
    enabled: bool


class CRMActionRequest(BaseModel):
    action: CRMActionType
    partner: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=4_000)
    to_stage: CRMStage | None = None
    occurred_at: datetime | None = None
    provenance: dict[str, object] = Field(default_factory=dict)
    action_id: str | None = Field(default=None, min_length=1, max_length=200)


class RecommendationLifecycleRequest(BaseModel):
    status: RecommendationLifecycleStatus
    actor: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=4_000)


class AlertLifecycleRequest(BaseModel):
    status: AlertLifecycleStatus
    actor: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=4_000)


class SourcePolicyResponse(BaseModel):
    providers: list[ProviderPolicy]
    connectors: list[ProviderPolicy]
    inventory: list[ConnectorInventoryItem]
    watchlists: list[WatchlistEntry] = Field(default_factory=list)
    runtime: list["SourceRuntimeStatus"] = Field(default_factory=list)
    direct_social_access_enabled: bool = False
    discovery_boundary: str


class SourceRuntimeStatus(BaseModel):
    source_id: str
    enabled: bool
    health: str = "not_checked"
    last_checked_at: str | None = None
    last_successful_fetch_at: str | None = None
    new_item_count: int = 0
    manually_overridden: bool = False
    note: str | None = None
    configuration_status: str = "not_required"
    credential_present: bool | None = None


class SourceSettingRequest(BaseModel):
    enabled: bool


class TemporalFieldContract(BaseModel):
    field: str
    meaning: str
    required: bool


class TemporalContractResponse(BaseModel):
    schema_version: str = "1.1"
    default_historical_cutoff_field: str = "observed_at"
    fields: list[TemporalFieldContract]
    invariant: str
    replay_available: bool = True


class SearchSuggestion(BaseModel):
    id: str
    kind: str
    label: str
    context: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    route: str


class ProgramListResponse(BaseModel):
    programs: list[ProgramConfig]


def _knowledge_snapshot(entity_id: str) -> tuple[dict, dict]:
    compiler = KnowledgeCompiler(settings.knowledge_dir)
    page = compiler.find(entity_id)
    metadata = compiler.polling_metadata(page)
    payload = page.model_dump(mode="json") if page is not None else None
    return {"entity_id": entity_id, "knowledge": payload, "revision": metadata}, metadata


@router.get("/knowledge/{entity_id}")
async def knowledge_snapshot(entity_id: str) -> dict:
    """Polling-friendly JSON equivalent of the knowledge SSE snapshot."""
    if is_generic_label(entity_id):
        raise HTTPException(404, "Validated knowledge entity not found")
    payload, _ = _knowledge_snapshot(entity_id)
    return payload


@router.get("/programs", response_model=ProgramListResponse)
async def list_programs() -> ProgramListResponse:
    return ProgramListResponse(programs=get_program_registry().active())


@router.get("/sources", response_model=SourcePolicyResponse)
async def list_source_policies() -> SourcePolicyResponse:
    """Return non-secret, machine-readable source access policy metadata."""
    policies = [policy.model_copy(deep=True) for policy in BUILTIN_PROVIDER_POLICIES]
    overrides_store = SourceSettingsStore(settings.source_settings_path)
    overrides = overrides_store.load()
    source_states = SourceStateStore(settings.source_state_path).load()
    try:
        report = SourceQualityReportStore(settings.source_quality_report_path).load()
    except (OSError, ValueError):
        report = None
    quality = {}
    for item in report.source_diagnostics if report is not None else []:
        source_key = item.source_id.split(":", 1)[0]
        existing = quality.get(source_key)
        if existing is None or item.known_positive_control:
            quality[source_key] = item
    watchlist = IntelligenceWatchlist.load(settings.intelligence_watchlist_path)
    watchlist_urls = {
        "techcrunch": [
            url
            for item in watchlist.active("publication")
            if (item.organization or "").casefold() == "techcrunch"
            for url in item.feed_urls
        ],
        "newsletter_watchlist": [
            url for item in watchlist.active("newsletter") for url in item.feed_urls
        ],
        "vc_watchlist": [
            url for item in watchlist.active("vc_firm", "vc_partner") for url in item.public_urls
        ],
    }
    runtime: list[SourceRuntimeStatus] = []
    for policy in policies:
        states = [
            source_states[url]
            for url in watchlist_urls.get(policy.provider_id, [])
            if url in source_states
        ]
        diagnostic = quality.get(policy.provider_id)
        last_checked = max(
            (item.last_checked_at for item in states if item.last_checked_at),
            default=None,
        )
        last_success = max(
            (item.last_successful_fetch_at for item in states if item.last_successful_fetch_at),
            default=None,
        )
        health = (
            diagnostic.state.value
            if diagnostic is not None
            else "healthy"
            if states and all(item.parse_health == "healthy" for item in states)
            else "degraded"
            if states
            else "not_checked"
        )
        runtime.append(
            SourceRuntimeStatus(
                source_id=policy.provider_id,
                enabled=overrides_store.enabled(policy.provider_id, policy.default_enabled),
                health=health,
                last_checked_at=last_checked,
                last_successful_fetch_at=last_success,
                new_item_count=sum(item.new_item_count for item in states),
                manually_overridden=policy.provider_id in overrides,
                note=(diagnostic.error if diagnostic is not None else None),
            )
        )
    product_hunt_token = settings.product_hunt_access_token
    product_hunt_credential = bool(
        product_hunt_token and product_hunt_token.get_secret_value().strip()
    )
    product_hunt_diagnostic = quality.get("product_hunt")
    product_hunt_checked_at = (
        report.finished_at.isoformat()
        if report is not None and product_hunt_diagnostic is not None
        else None
    )
    product_hunt_health = (
        product_hunt_diagnostic.state.value
        if product_hunt_diagnostic is not None
        else "not_checked"
    )
    runtime.append(
        SourceRuntimeStatus(
            source_id="product_hunt",
            enabled=settings.product_hunt_api_enabled,
            configuration_status=(
                "configured"
                if settings.product_hunt_api_enabled and product_hunt_credential
                else "not_configured"
                if settings.product_hunt_api_enabled
                else "disabled"
            ),
            credential_present=product_hunt_credential,
            health=product_hunt_health,
            last_checked_at=product_hunt_checked_at,
            last_successful_fetch_at=(
                product_hunt_checked_at if product_hunt_health == "healthy" else None
            ),
            note=(
                product_hunt_diagnostic.error
                if product_hunt_diagnostic is not None
                else None
            ),
        )
    )
    return SourcePolicyResponse(
        providers=policies,
        connectors=[
            policy.model_copy(deep=True)
            for policy in (*CONNECTOR_POLICIES, *OPTIONAL_INTEGRATION_POLICIES)
        ],
        inventory=build_connector_inventory(
            report.source_diagnostics if report is not None else [],
            report.dedicated_osint_tools if report is not None else [],
        ),
        watchlists=watchlist.active(),
        runtime=runtime,
        discovery_boundary=(
            "Permitted search provider -> indexed social result is discovery-only; "
            "direct LinkedIn/X/Instagram/Threads website access is disabled by default."
        ),
    )


@router.put("/sources/{source_id}", response_model=SourceRuntimeStatus)
async def update_source_setting(
    source_id: str,
    request: SourceSettingRequest,
) -> SourceRuntimeStatus:
    """Persist an operator override without conflating enabled state with health."""
    policy = next(
        (item for item in BUILTIN_PROVIDER_POLICIES if item.provider_id == source_id),
        None,
    )
    if policy is None:
        raise HTTPException(404, "Unknown configurable source")
    SourceSettingsStore(settings.source_settings_path).set_enabled(source_id, request.enabled)
    return SourceRuntimeStatus(
        source_id=source_id,
        enabled=request.enabled,
        health="not_checked",
        manually_overridden=True,
        note="Manual source setting saved; health is measured independently on the next run.",
    )


@router.get(
    "/source-quality/contract",
    response_model=OperationalValidationContract,
)
async def source_quality_contract() -> OperationalValidationContract:
    """Describe the explicit local-only gate for bounded live validation."""
    return OperationalValidationContract(
        cli_command=(
            "VENTURE_INTELLIGENCE_LIVE_TESTS=1 uv run python -m "
            "venture-intelligence --json sources check --program <program-id> --allow-network"
        ),
        metrics_without_ground_truth=[
            "discovery yield",
            "source contribution",
            "source ablation promotion loss",
            "blocks, drift, latency, retries, and request counts",
        ],
        metrics_requiring_ground_truth=[
            "precision at K",
            "recall",
            "false-positive rate",
            "lead time",
        ],
    )


@router.get("/source-quality", response_model=SourceQualityResponse)
async def source_quality() -> SourceQualityResponse:
    """Return the latest persisted live report; never trigger network access."""
    try:
        report = SourceQualityReportStore(settings.source_quality_report_path).load()
    except (OSError, ValueError) as error:
        return SourceQualityResponse(
            available=False,
            message=f"Stored source-quality report is invalid: {str(error)[:300]}",
        )
    return SourceQualityResponse(
        available=report is not None,
        report=report,
        message=(
            "Latest gated live validation report."
            if report is not None
            else "No gated live validation report has been produced on this installation."
        ),
    )


@router.get("/system/readiness", response_model=SystemReadiness)
async def product_readiness() -> SystemReadiness:
    """Return one non-secret product readiness contract for UI and CLI."""
    return system_readiness()


@router.get("/cycles", response_model=OperationsState)
async def intelligence_cycles() -> OperationsState:
    return OperationsStateStore(settings.operations_state_path).load()


@router.get("/cycles/{cycle_id}", response_model=IntelligenceCycleRecord)
async def intelligence_cycle(cycle_id: str) -> IntelligenceCycleRecord:
    state = OperationsStateStore(settings.operations_state_path).load()
    match = next((item for item in state.cycles if item.cycle_id == cycle_id), None)
    if match is None:
        raise HTTPException(404, "Intelligence cycle not found")
    return match


@router.post("/cycles/run", response_model=IntelligenceCycleRecord)
async def run_intelligence_cycle(request: CycleRunRequest) -> IntelligenceCycleRecord:
    try:
        return await IntelligenceCycleService().run_once(
            program_ids=request.program_ids,
            trigger="api",
        )
    except CycleAlreadyRunning as error:
        raise HTTPException(409, str(error)) from error


@router.put("/scheduler", response_model=SchedulerState)
async def configure_scheduler(request: SchedulerControlRequest) -> SchedulerState:
    return IntelligenceCycleService().set_scheduler_enabled(request.enabled)


@router.get("/system/temporal-contract", response_model=TemporalContractResponse)
async def temporal_contract() -> TemporalContractResponse:
    """Publish authoritative time semantics shared by live and replay paths."""
    return TemporalContractResponse(
        fields=[
            TemporalFieldContract(
                field="occurred_at",
                meaning="When the source states the event happened; may be unknown or approximate.",
                required=True,
            ),
            TemporalFieldContract(
                field="observed_at",
                meaning="When this system first observed the source evidence.",
                required=True,
            ),
            TemporalFieldContract(
                field="ingested_at",
                meaning="When the canonical event was persisted.",
                required=True,
            ),
            TemporalFieldContract(
                field="validity",
                meaning=(
                    "Optional explicit fact-validity interval; never inferred "
                    "from observation time."
                ),
                required=False,
            ),
        ],
        invariant=(
            "observed_at is the historical-cutoff boundary; occurred_at and validity "
            "must not be fabricated from retrieval time"
        ),
    )


@router.get("/replay/contract", response_model=ReplayContractResponse)
async def replay_contract() -> ReplayContractResponse:
    """Describe local replay semantics without exposing remote execution."""
    return ReplayContractResponse(
        cli_command=(
            "uv run python -m venture_intelligence.replay.cli --until <ISO-8601-with-offset>"
        ),
        side_effects_suppressed=SUPPRESSED_REPLAY_SIDE_EFFECTS,
    )


@router.get("/graph", response_model=GraphSnapshotResponse)
async def temporal_graph(
    as_of: datetime | None = None,
    program_id: str | None = None,
    node_types: str | None = None,
    min_confidence: float = Query(default=0.0, ge=0, le=1),
    focus_id: str | None = Query(default=None, max_length=200),
    depth: int = Query(default=1, ge=0, le=2),
    max_nodes: int = Query(default=200, ge=1, le=400),
    max_edges: int = Query(default=400, ge=1, le=800),
) -> GraphSnapshotResponse:
    """Return a bounded integrity-gated live or isolated historical graph."""
    if program_id and get_program_registry().get(program_id) is None:
        raise HTTPException(404, f"Unknown program id: {program_id}")
    parsed_types: set[GraphNodeType] | None = None
    if node_types:
        try:
            parsed_types = {
                GraphNodeType(item.strip()) for item in node_types.split(",") if item.strip()
            }
        except ValueError as error:
            raise HTTPException(
                422, "node_types contains an unsupported graph node type"
            ) from error
    try:
        return GraphService(Database(settings.db_path)).snapshot(
            as_of=as_of,
            program_id=program_id,
            node_types=parsed_types,
            min_confidence=min_confidence,
            focus_id=focus_id,
            depth=depth,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/evaluations/contract", response_model=EvaluationContractResponse)
async def evaluation_contract() -> EvaluationContractResponse:
    """Describe the local-only, isolated historical evaluation boundary."""
    return EvaluationContractResponse(
        import_cli_command=(
            "uv run python -m venture_intelligence.evaluation.cli --database <db> "
            "import --file <ground-truth.json>"
        ),
        run_cli_command=(
            "uv run python -m venture_intelligence.evaluation.cli --database <db> "
            "run --ground-truth <dataset-id> --cutoff <ISO-8601-with-offset>"
        ),
        side_effects_suppressed=SUPPRESSED_REPLAY_SIDE_EFFECTS,
    )


@router.get("/evaluations", response_model=EvaluationListResponse)
async def list_evaluations() -> EvaluationListResponse:
    """List persisted deterministic reports; evaluation execution is local-only."""
    return EvaluationService(Database(settings.db_path)).list_runs()


@router.get("/evaluations/{evaluation_run_id}", response_model=EvaluationRun)
async def get_evaluation(evaluation_run_id: str) -> EvaluationRun:
    """Return one persisted evaluation report with diagnostics and provenance."""
    result = Database(settings.db_path).get_evaluation_run(evaluation_run_id)
    if result is None:
        raise HTTPException(404, "Evaluation run not found")
    return result


@router.get(
    "/intelligence/rankings",
    response_model=RankingIntelligenceResponse,
)
async def ranking_intelligence(
    as_of: datetime | None = None,
    entity_type: str | None = None,
    program_id: str | None = None,
    batch_id: str | None = None,
    limit: int = 100,
) -> RankingIntelligenceResponse:
    """Return integrity-gated rankings with exact feature/signal lineage."""
    if entity_type not in {None, "person", "company"}:
        raise HTTPException(422, "entity_type must be 'person' or 'company'")
    if program_id and get_program_registry().get(program_id) is None:
        raise HTTPException(404, f"Unknown program id: {program_id}")
    try:
        return IntelligenceService(Database(settings.db_path)).rankings(
            as_of=as_of,
            entity_type=entity_type,
            program_id=program_id,
            batch_id=batch_id,
            limit=max(1, min(limit, 200)),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get(
    "/intelligence/entities/{entity_id}",
    response_model=EntityIntelligenceResponse,
)
async def entity_intelligence(
    entity_id: str,
    as_of: datetime | None = None,
) -> EntityIntelligenceResponse:
    """Return one validated entity's evidence-to-recommendation lineage."""
    try:
        result = IntelligenceService(Database(settings.db_path)).entity(
            entity_id,
            as_of=as_of,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if result is None:
        raise HTTPException(404, "Validated entity intelligence not found")
    return result


@router.get("/decisions", response_model=DecisionBoardResponse)
async def decision_board(
    as_of: datetime | None = None,
    program_id: str | None = None,
) -> DecisionBoardResponse:
    """Return the integrity-gated, historically bounded CRM decision board."""
    try:
        if program_id and get_program_registry().get(program_id) is None:
            raise HTTPException(404, f"Unknown program id: {program_id}")
        return DecisionService(Database(settings.db_path)).board(as_of=as_of, program_id=program_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get(
    "/decisions/entities/{entity_id}",
    response_model=DecisionEntityResponse,
)
async def decision_entity(
    entity_id: str,
    as_of: datetime | None = None,
) -> DecisionEntityResponse:
    """Return one entity's CRM actions, feedback lineage, and decision lifecycle."""
    try:
        result = DecisionService(Database(settings.db_path)).entity(entity_id, as_of=as_of)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if result is None:
        raise HTTPException(404, "Validated CRM decision entity not found")
    return result


@router.post(
    "/decisions/entities/{entity_id}/actions",
    response_model=CRMActionResult,
)
async def record_crm_action(
    entity_id: str,
    request: CRMActionRequest,
) -> CRMActionResult:
    """Append human feedback and deterministically rebuild downstream lineage."""
    try:
        return DecisionService(Database(settings.db_path)).apply_action(
            entity_id,
            request.action,
            partner=request.partner,
            note=request.note,
            to_stage=request.to_stage,
            occurred_at=request.occurred_at,
            provenance={"channel": "api", **request.provenance},
            action_id=request.action_id,
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post(
    "/decisions/recommendations/{recommendation_id}/lifecycle",
    response_model=RecommendationLifecycleEvent,
)
async def update_recommendation_lifecycle(
    recommendation_id: str,
    request: RecommendationLifecycleRequest,
) -> RecommendationLifecycleEvent:
    allowed = {
        RecommendationLifecycleStatus.ACCEPTED,
        RecommendationLifecycleStatus.DISMISSED,
        RecommendationLifecycleStatus.COMPLETED,
    }
    if request.status not in allowed:
        raise HTTPException(422, "Only human decision lifecycle states may be posted")
    try:
        return DecisionService(Database(settings.db_path)).update_recommendation(
            recommendation_id,
            request.status,
            actor=request.actor,
            reason=request.reason,
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post(
    "/decisions/alerts/{alert_id}/lifecycle",
    response_model=AlertLifecycleEvent,
)
async def update_alert_lifecycle(
    alert_id: str,
    request: AlertLifecycleRequest,
) -> AlertLifecycleEvent:
    allowed = {
        AlertLifecycleStatus.ACKNOWLEDGED,
        AlertLifecycleStatus.DISMISSED,
        AlertLifecycleStatus.RESOLVED,
    }
    if request.status not in allowed:
        raise HTTPException(422, "Only human alert lifecycle states may be posted")
    try:
        return DecisionService(Database(settings.db_path)).update_alert(
            alert_id,
            request.status,
            actor=request.actor,
            reason=request.reason,
        )
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/search", response_model=list[SearchSuggestion])
async def command_search(q: str, limit: int = 12) -> list[SearchSuggestion]:
    """Search programs and integrity-validated canonical entities only."""
    query = q.strip()
    if not query:
        return []
    bounded = max(1, min(limit, 30))
    folded = query.casefold()
    terms = folded.split()
    suggestions: list[SearchSuggestion] = []
    for program in get_program_registry().active():
        program_index = " ".join(
            [
                program.id,
                program.canonical_program_id or "",
                program.name,
                program.batch,
                program.current_batch or "",
                *program.keywords,
                *(
                    value
                    for batch in program.supported_batches
                    for value in [batch.id, batch.name, *batch.keywords]
                ),
            ]
        ).casefold()
        if all(term in program_index for term in terms):
            suggestions.append(
                SearchSuggestion(
                    id=program.id,
                    kind="program",
                    label=program.name,
                    context=f"{program.batch} · {program.detection_strategy}",
                    route="/discover?type=programs",
                )
            )
    database = Database(settings.db_path)
    for item in database.search_validated_entities(query, limit=bounded):
        if is_generic_label(item["label"]):
            continue
        suggestions.append(
            SearchSuggestion(
                id=item["entity_id"],
                kind=item["entity_type"],
                label=item["label"],
                context=item["context"],
                confidence=item["confidence"],
                route=f"/research/{item['entity_id']}",
            )
        )
    return suggestions[:bounded]


@router.post("/research", response_model=ResearchRun)
async def research(request: ResearchRequest) -> ResearchRun:
    """Run bounded free/public research and publish typed findings to SQLite."""
    if not request.entity_name.strip():
        raise HTTPException(422, "entity_name must not be blank")
    registry = get_program_registry()
    program = registry.get(request.program_id) if request.program_id else None
    if request.program_id and program is None:
        raise HTTPException(404, f"Unknown program id: {request.program_id}")
    target = ResearchTarget(
        name=request.entity_name.strip(),
        type=request.entity_type,
        program_id=program.id if program else None,
        aliases=list(dict.fromkeys([*request.aliases, *([program.name] if program else [])])),
        domains=list(
            dict.fromkeys(
                [
                    *request.domains,
                    *(
                        urlsplit(url).netloc
                        for url in (program.website_urls if program else [])
                        if urlsplit(url).netloc
                    ),
                ]
            )
        ),
    )
    database = Database(settings.db_path)
    bus = EventBus(database)
    program = get_program_registry().get(request.program_id) if request.program_id else None
    agent = build_research_agent(
        program_name=program.name if program else None,
        program_batch=program.batch if program else None,
    )
    try:
        return await agent.run(target, budget=request.budget, event_bus=bus)
    finally:
        await agent.close()


@router.post("/intel/gather", response_model=GatherResponse)
async def gather_intelligence(request: GatherRequest) -> GatherResponse:
    """Preview a bounded public corroboration pass without persistence."""
    registry = get_program_registry()
    program = registry.get(request.program_id) if request.program_id else None
    if request.program_id and program is None:
        raise HTTPException(404, f"Unknown program id: {request.program_id}")

    entity_name = request.entity_name.strip()
    if not entity_name:
        raise HTTPException(422, "entity_name must not be blank")
    entity_id = (
        registry.entity_id(program.id, entity_name) if program else f"entity__{_slug(entity_name)}"
    )
    trigger = CanonicalEvent(
        event_type=EventType.FOUNDER_DETECTED,
        entity_type=EntityType.COMPANY,
        entity_id=entity_id,
        source=Source.API,
        source_url="api://v1/intel/gather",
        occurred_at=datetime.now(timezone.utc),
        confidence=0.5,
        raw_payload={"name": entity_name},
        metadata={"entity_name": entity_name, "program_id": program.id if program else None},
    )
    connectors = build_connectors(registry, share_crawler=False)
    eligible = [connector for connector in connectors if connector.can_investigate(trigger)]
    operation_ids: dict[str, str | None] = {}
    tasks = {
        asyncio.create_task(_run_gather_operation(connector, trigger, operation_ids)): connector
        for connector in eligible
    }
    completed: dict[str, list[CanonicalEvent] | Exception | None] = {}
    try:
        done, pending = await asyncio.wait(
            tasks,
            timeout=settings.gather_timeout_seconds,
        )
        for task in done:
            connector = tasks[task]
            try:
                completed[connector.source_name] = task.result()
            except Exception as error:
                completed[connector.source_name] = error
        for task in pending:
            task.cancel()
            completed[tasks[task].source_name] = None
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await asyncio.gather(
            *(connector.close() for connector in connectors),
            return_exceptions=True,
        )

    source_outcomes = [
        _gather_source_outcome(
            connector,
            completed.get(connector.source_name),
            operation_id=operation_ids.get(connector.source_name),
        )
        for connector in eligible
    ]
    return GatherResponse(
        entity_name=entity_name,
        sources_considered=[item.source for item in source_outcomes],
        sources_attempted=[item.source for item in source_outcomes if item.attempted],
        outcomes=source_outcomes,
        events_found=sum(item.events_found for item in source_outcomes),
        events_accepted=sum(item.events_accepted for item in source_outcomes),
        events_rejected=sum(item.events_rejected for item in source_outcomes),
        gathered_at=datetime.now(timezone.utc),
    )


def _gather_source_outcome(
    connector,
    result: list[CanonicalEvent] | Exception | None,
    *,
    operation_id: str | None = None,
) -> GatherSourceOutcome:
    crawler = getattr(connector, "crawler", None)
    attempted = bool(getattr(crawler, "request_count", 0))
    crawl_outcomes = list(getattr(crawler, "request_outcomes", []))
    latest = crawl_outcomes[-1] if crawl_outcomes else {}
    recorder = getattr(crawler, "record_evidence_outcome", None)
    if recorder is not None:
        evidence_urls = (event.source_url for event in result) if isinstance(result, list) else ()
        if operation_id is None:
            recorder(evidence_urls)
        else:
            recorder(evidence_urls, operation_id=operation_id)
    if result is None:
        return GatherSourceOutcome(
            source=connector.source_name,
            attempted=attempted,
            status=GatherSourceStatus.TIMEOUT,
            error_category="timeout",
            final_url=latest.get("url"),
            http_status=latest.get("status_code"),
        )
    if isinstance(result, Exception):
        return GatherSourceOutcome(
            source=connector.source_name,
            attempted=attempted,
            status=GatherSourceStatus.FAILED,
            error_category=result.__class__.__name__,
            final_url=latest.get("url"),
            http_status=latest.get("status_code"),
        )

    validations = [validate_candidate(event) for event in result]
    accepted = sum(item.accepted for item in validations)
    rejected = len(validations) - accepted
    rejection_reasons = sorted(
        {item.reason for item in validations if not item.accepted and item.reason}
    )
    if accepted:
        status = GatherSourceStatus.SUCCESS
        error_category = None
    elif rejected:
        status = GatherSourceStatus.CANDIDATE_REJECTED
        error_category = ",".join(rejection_reasons) or "candidate_rejected"
    elif not attempted:
        status = GatherSourceStatus.SKIPPED
        error_category = "not_applicable"
    elif latest.get("usable"):
        status = GatherSourceStatus.SUCCESS_EMPTY
        error_category = None
    else:
        status = str(latest.get("source_status") or GatherSourceStatus.FAILED.value)
        error_category = str(latest.get("error") or "source_unavailable")
    return GatherSourceOutcome(
        source=connector.source_name,
        attempted=attempted,
        status=status,
        events_found=len(result),
        events_accepted=accepted,
        events_rejected=rejected,
        error_category=error_category,
        final_url=latest.get("url"),
        http_status=latest.get("status_code"),
    )


async def _run_gather_operation(connector, trigger, operation_ids):
    """Investigate with request outcomes attributed to this connector task."""
    crawler = getattr(connector, "crawler", None)
    begin = getattr(crawler, "begin_evidence_operation", None)
    end = getattr(crawler, "end_evidence_operation", None)
    operation_id = None
    token = None
    if begin is not None and end is not None:
        operation_id, token = begin()
    operation_ids[connector.source_name] = operation_id
    try:
        try:
            return await connector.investigate(trigger)
        except Exception as exc:
            return exc
    finally:
        if token is not None:
            end(token)


def _slug(value: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "unknown"
