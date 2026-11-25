"""Knowledge pages, validation, and bounded SSE revision streams."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from venture_intelligence.config import settings
from venture_intelligence.knowledge.compiler import KnowledgeCompiler
from venture_intelligence.knowledge.models import KnowledgePage
from venture_intelligence.pipeline.entity_integrity import is_generic_label

router = APIRouter(prefix="/api/v1", tags=["v1"])
legacy_router = APIRouter()


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


_SSE_MAX_FRAMES = 12
_SSE_MAX_FRAME_BYTES = 256_000


def _sse_json_frame(event: str, payload: dict, *, event_id: str | None = None) -> str:
    """Serialize one bounded SSE frame; never stream an oversized JSON page."""
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    prefix = f"id: {event_id}\n" if event_id else ""
    frame = f"{prefix}event: {event}\ndata: {data}\n\n"
    if len(frame.encode("utf-8")) <= _SSE_MAX_FRAME_BYTES:
        return frame
    fallback = json.dumps(
        {"type": "stream.error", "error": "sse_frame_too_large"},
        separators=(",", ":"),
    )
    return f"event: stream.error\ndata: {fallback}\n\n"


@legacy_router.get("/api/knowledge/validate")
async def knowledge_validate() -> dict:
    """Validate canonical JSON pages and their claim/evidence references."""
    return dict(KnowledgeCompiler(settings.knowledge_dir).validate())


@legacy_router.get("/api/knowledge/stream/{entity_id}")
@legacy_router.get("/api/v1/knowledge/stream/{entity_id}")
async def knowledge_stream(entity_id: str) -> StreamingResponse:
    """Bounded SSE stream of JSON wiki revisions.

    The stream sends an initial snapshot, then at most twelve revision/heartbeat
    frames.  Clients must reconnect or use the ordinary JSON page endpoint for
    long-lived polling; this prevents idle connections from becoming an
    unbounded resource.
    """
    compiler = KnowledgeCompiler(settings.knowledge_dir)

    async def frames():
        last_etag: str | None = None
        for _ in range(_SSE_MAX_FRAMES):
            page = compiler.find(entity_id)
            metadata = compiler.polling_metadata(page)
            if page is None:
                payload = {
                    "type": "knowledge.unavailable",
                    "entity_id": entity_id,
                    "metadata": metadata,
                }
                yield _sse_json_frame("knowledge.unavailable", payload)
                return
            if metadata["etag"] != last_etag:
                last_etag = metadata["etag"]
                payload = {
                    "type": "knowledge.updated",
                    "entity_id": entity_id,
                    "revision": metadata,
                    "page": page.model_dump(mode="json"),
                }
                yield _sse_json_frame(
                    "knowledge.updated", payload, event_id=str(metadata["revision_id"])
                )
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(min(5, max(1, int(metadata.get("poll_after_seconds", 5)))))

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Content-Type-Options": "nosniff",
            "X-Accel-Buffering": "no",
        },
    )


@legacy_router.get("/api/events/stream")
@legacy_router.get("/api/v1/events/stream")
async def dashboard_event_stream() -> StreamingResponse:
    """Bounded dashboard-wide revision hint stream used by graph/CRM views."""
    compiler = KnowledgeCompiler(settings.knowledge_dir)

    async def frames():
        last_etag: str | None = None
        for _ in range(_SSE_MAX_FRAMES):
            pages = []
            for path in sorted(compiler.root.glob("*/*.json")):
                try:
                    page = compiler.load(path.parent.name, path.stem)
                except (OSError, ValueError):
                    page = None
                if page is not None:
                    pages.append(compiler.polling_metadata(page))
            digest = json.dumps(pages, sort_keys=True, separators=(",", ":"))
            if digest != last_etag:
                last_etag = digest
                yield _sse_json_frame(
                    "dashboard.updated", {"type": "dashboard.updated", "pages": pages}
                )
            else:
                yield ": heartbeat\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Content-Type-Options": "nosniff",
            "X-Accel-Buffering": "no",
        },
    )


@legacy_router.get("/api/knowledge/{entity_type}/{entity_id}", response_model=KnowledgePage)
async def knowledge_page(entity_type: str, entity_id: str) -> KnowledgePage:
    page = KnowledgeCompiler(settings.knowledge_dir).load(entity_type, entity_id)
    if page is None:
        raise HTTPException(404, "knowledge page not found")
    return page
