"""Browser WebRTC demo endpoint.

Lightweight signaling over WebSocket for offer/answer/ICE exchange. The
heavy lifting (codec negotiation, SRTP) lives in the browser; the server
forwards 16 kHz PCM audio frames to the same orchestrator that powers the
Twilio path, so demos and real calls share one code path.

Hardening matches `/voice/stream`:
- A per-process `CallGate` caps simultaneous sessions. If the gate is full
  we reject the socket with code 1013 (try again later).
- Frames that aren't well-formed JSON are logged and skipped.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.concurrency import CallGate
from app.logging import get_logger
from app.pipeline.orchestrator import CallOrchestrator

router = APIRouter()
log = get_logger(__name__)

WS_TRY_AGAIN_LATER = 1013


@router.websocket("/signal")
async def signaling(ws: WebSocket) -> None:
    gate: CallGate = ws.app.state.call_gate
    if not gate.try_acquire_nowait():
        await ws.accept()
        await ws.close(code=WS_TRY_AGAIN_LATER, reason="server busy")
        log.warning("webrtc.gate.full", active=gate.active, max=gate.max)
        return

    try:
        await ws.accept()
        await _run_signaling(ws)
    except WebSocketDisconnect:
        log.info("webrtc.disconnect")
    finally:
        gate.release()


async def _run_signaling(ws: WebSocket) -> None:
    orchestrator: CallOrchestrator | None = None
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("webrtc.bad_json")
                continue

            kind = msg.get("type")

            if kind == "start":
                session_id = str(msg.get("session_id", "webrtc"))
                structlog.contextvars.bind_contextvars(session_id=session_id)
                orchestrator = CallOrchestrator(
                    websocket=ws,
                    stream_sid=session_id,
                    call_sid=session_id,
                    from_number="webrtc",
                    transport="webrtc",
                )
                await orchestrator.start()

            elif kind == "audio" and orchestrator is not None:
                payload = msg.get("payload")
                if isinstance(payload, str):
                    await orchestrator.on_audio_frame(payload, encoding="pcm16")

            elif kind == "stop":
                break
    finally:
        if orchestrator is not None:
            await orchestrator.stop()
        structlog.contextvars.unbind_contextvars("session_id")
