"""Browser WebRTC demo endpoint.

Lightweight signaling using WebSocket for offer/answer/ICE exchange. The
heavy lifting (codec negotiation, SRTP) lives in the browser; the server
forwards 16 kHz PCM audio frames to the same orchestrator that powers the
Twilio path so demos and real calls share one code path.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.logging import get_logger
from app.pipeline.orchestrator import CallOrchestrator

router = APIRouter()
log = get_logger(__name__)


@router.websocket("/signal")
async def signaling(ws: WebSocket) -> None:
    await ws.accept()
    orchestrator: CallOrchestrator | None = None

    try:
        while True:
            raw = await ws.receive_text()
            msg: dict[str, Any] = json.loads(raw)
            kind = msg.get("type")

            if kind == "start":
                orchestrator = CallOrchestrator(
                    websocket=ws,
                    stream_sid=msg.get("session_id", "webrtc"),
                    call_sid=msg.get("session_id", "webrtc"),
                    from_number="webrtc",
                    transport="webrtc",
                )
                await orchestrator.start()

            elif kind == "audio" and orchestrator is not None:
                # base64 PCM16 mono 16kHz
                await orchestrator.on_audio_frame(msg["payload"], encoding="pcm16")

            elif kind == "stop":
                break

    except WebSocketDisconnect:
        log.info("webrtc.disconnect")
    finally:
        if orchestrator is not None:
            await orchestrator.stop()
