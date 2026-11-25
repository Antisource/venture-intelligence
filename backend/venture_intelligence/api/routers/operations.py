"""Intelligence cycles, scheduler control, and the temporal contract."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from venture_intelligence.config import settings
from venture_intelligence.operations.cycle import (
    CycleAlreadyRunning,
    IntelligenceCycleRecord,
    IntelligenceCycleService,
    OperationsState,
    OperationsStateStore,
    SchedulerState,
)

router = APIRouter(prefix="/api/v1", tags=["v1"])


class CycleRunRequest(BaseModel):
    program_ids: list[str] | None = None


class SchedulerControlRequest(BaseModel):
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
