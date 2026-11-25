"""Accelerator program registry endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from venture_intelligence.programs import ProgramConfig, get_program_registry

router = APIRouter(prefix="/api/v1", tags=["v1"])
legacy_router = APIRouter()


class ProgramListResponse(BaseModel):
    programs: list[ProgramConfig]


@router.get("/programs", response_model=ProgramListResponse)
async def list_programs() -> ProgramListResponse:
    return ProgramListResponse(programs=get_program_registry().active())


@legacy_router.get("/api/programs")
async def legacy_programs() -> dict:
    """Unversioned alias retained for the dashboard migration."""
    return await list_programs()
