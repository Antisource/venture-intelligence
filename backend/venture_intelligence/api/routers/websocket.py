"""Small health/stats WebSocket channel retained for dashboard clients."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from venture_intelligence.api.routers.ops import stats

router = APIRouter()

_WS_MAX_MESSAGES = 120
_WS_MAX_MESSAGE_CHARS = 512
_WS_IDLE_TIMEOUT_SECONDS = 300


@router.websocket("/ws")
@router.websocket("/api/ws")
async def websocket_updates(websocket: WebSocket) -> None:
    """Small health/stats channel retained for dashboard clients."""
    await websocket.accept()
    message_count = 0
    try:
        await websocket.send_json({"type": "connected", "project": "venture-intelligence"})
        while True:
            if message_count >= _WS_MAX_MESSAGES:
                await websocket.close(code=1008, reason="message limit exceeded")
                return
            try:
                raw_message = await asyncio.wait_for(
                    websocket.receive_text(), timeout=_WS_IDLE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                await websocket.close(code=1000, reason="idle timeout")
                return
            if len(raw_message) > _WS_MAX_MESSAGE_CHARS:
                await websocket.close(code=1009, reason="message too large")
                return
            message_count += 1
            message = raw_message.strip().lower()
            if message in {"ping", '{"type":"ping"}'}:
                await websocket.send_json({"type": "pong"})
            elif message in {"stats", '{"type":"stats"}'}:
                await websocket.send_json({"type": "stats", "data": await stats()})
            else:
                await websocket.send_json({"type": "ack", "message": message[:200]})
    except WebSocketDisconnect:
        return
