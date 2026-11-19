"""Replay and evaluation contracts plus the legacy evaluation feed."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from venture_intelligence.api.routers.dependencies import _get_db
from venture_intelligence.config import settings
from venture_intelligence.evaluation.models import (
    EvaluationContractResponse,
    EvaluationListResponse,
    EvaluationRun,
)
from venture_intelligence.evaluation.service import EvaluationService
from venture_intelligence.replay.models import ReplayContractResponse
from venture_intelligence.replay.service import SUPPRESSED_REPLAY_SIDE_EFFECTS
from venture_intelligence.storage.db import Database

replay_router = APIRouter(prefix="/api/v1", tags=["v1"])
router = APIRouter(prefix="/api/v1", tags=["v1"])
legacy_router = APIRouter()


@replay_router.get("/replay/contract", response_model=ReplayContractResponse)
async def replay_contract() -> ReplayContractResponse:
    """Describe local replay semantics without exposing remote execution."""
    return ReplayContractResponse(
        cli_command=(
            "uv run python -m venture_intelligence.replay.cli --until <ISO-8601-with-offset>"
        ),
        side_effects_suppressed=SUPPRESSED_REPLAY_SIDE_EFFECTS,
    )


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


@legacy_router.get("/api/evaluation")
async def evaluation() -> dict:
    """Return persisted evaluation results for the dashboard."""
    return {"evaluations": _get_db().get_evaluations()}
