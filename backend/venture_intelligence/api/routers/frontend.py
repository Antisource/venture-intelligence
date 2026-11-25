"""Built React application entry point and HashRouter link fallback."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from venture_intelligence.api.routers.dependencies import _accepts_html

router = APIRouter()
fallback_router = APIRouter()

_frontend_root = Path(__file__).resolve().parents[4] / "frontend"
_frontend_dist = _frontend_root / "dist" / "index.html"


@router.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    if not _frontend_dist.is_file():
        raise HTTPException(
            503,
            "React production build is unavailable; run `pnpm build`. "
            "Use `pnpm dev` for frontend development.",
        )
    return FileResponse(_frontend_dist)


@fallback_router.get("/{spa_path:path}", include_in_schema=False)
async def spa_fallback(request: Request, spa_path: str):
    """Redirect clean links into HashRouter without losing investigation context."""
    if spa_path.startswith(("api/", "frontend/")) or not _accepts_html(request):
        raise HTTPException(404, "Not found")
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/#/{spa_path}{query}", status_code=307)
