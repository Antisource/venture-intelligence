"""Capability registry endpoint: what each lens can do and what higher lenses add."""

from __future__ import annotations

from fastapi import APIRouter, Query

from venture_intelligence.capabilities import Lens, LensCapabilities, capabilities_for

router = APIRouter(prefix="/api/v1", tags=["v1"])


@router.get("/capabilities", response_model=LensCapabilities)
async def list_capabilities(
    lens: Lens = Query(
        Lens.L1,
        description="Lens to view: L1 Explorer, L2 Analyst, L3 Partner, L4 Technical VC.",
    ),
) -> LensCapabilities:
    """Return the capabilities visible to a lens plus teasers for higher lenses."""
    return capabilities_for(lens)
