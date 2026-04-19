"""Twilio Programmable Voice + Media Streams entrypoints.

POST /voice/incoming   → returns TwiML that opens a bidirectional Media Stream.
WS   /voice/stream     → consumes μ-law/8kHz frames, runs the agent pipeline,
                         and streams synthesized audio back to the caller.

Hardening:
- Twilio webhook signatures are validated on `/voice/incoming` (disabled
  only by opt-in via `twilio_validate_signature=false`).
- A per-process `CallGate` caps simultaneous Media Streams sessions. When
  the gate is full, we return TwiML that politely ends the call.
- WebSocket frames are parsed defensively; anything that isn't a well-
  formed Twilio event is logged and ignored.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.concurrency import CallGate
from app.config import Settings, get_settings
from app.logging import get_logger
from app.pipeline.orchestrator import CallOrchestrator
from app.security import validate_twilio_signature

router = APIRouter()
log = get_logger(__name__)


def _public_ws_url(settings: Settings) -> str:
    return (
        settings.public_base_url.replace("https://", "wss://").replace("http://", "ws://")
        + "/voice/stream"
    )


_BUSY_TWIML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<Response>"
    "<Say>All our agents are busy right now. Please try again in a few minutes.</Say>"
    "<Hangup/>"
    "</Response>"
)


@router.post("/incoming")
async def incoming_call(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Twilio webhook for an inbound PSTN call.

    Returns TwiML that connects the call to our WebSocket endpoint via
    `<Stream>` so we receive μ-law frames and can talk back. If the call
    gate is full we short-circuit with a polite hangup.
    """
    await validate_twilio_signature(request, settings)

    form = await request.form()
    call_sid = str(form.get("CallSid", "unknown"))
    from_number = str(form.get("From", "unknown"))

    gate: CallGate = request.app.state.call_gate
    if gate.active >= gate.max:
        log.warning("twilio.gate.full", active=gate.active, max=gate.max)
        return Response(content=_BUSY_TWIML, media_type="application/xml")

    log.info("twilio.incoming", call_sid=call_sid, from_number=from_number)

    ws_url = _public_ws_url(settings)
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}">
      <Parameter name="callSid" value="{call_sid}" />
      <Parameter name="from" value="{from_number}" />
    </Stream>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.websocket("/stream")
async def media_stream(ws: WebSocket) -> None:
    """Twilio Media Streams WebSocket.

    Frame protocol (JSON over text frames):
      { "event": "start",  "start": {...}, "streamSid": "..." }
      { "event": "media",  "media": { "payload": "<base64 μ-law>" } }
      { "event": "stop",   "stop":  {...} }
    """
    await ws.accept()
    gate: CallGate = ws.app.state.call_gate

    try:
        async with gate.slot():
            await _run_media_stream(ws)
    except WebSocketDisconnect:
        log.info("twilio.stream.disconnect")


async def _run_media_stream(ws: WebSocket) -> None:
    orchestrator: CallOrchestrator | None = None
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("twilio.stream.bad_json")
                continue

            event = msg.get("event")

            if event == "start":
                start = msg.get("start") or {}
                stream_sid = str(start.get("streamSid", ""))
                params = start.get("customParameters") or {}
                call_sid = str(params.get("callSid") or stream_sid)
                from_number = str(params.get("from", "unknown"))
                structlog.contextvars.bind_contextvars(call_sid=call_sid, stream_sid=stream_sid)
                orchestrator = CallOrchestrator(
                    websocket=ws,
                    stream_sid=stream_sid,
                    call_sid=call_sid,
                    from_number=from_number,
                )
                await orchestrator.start()
                log.info("twilio.stream.start")

            elif event == "media" and orchestrator is not None:
                payload = (msg.get("media") or {}).get("payload")
                if isinstance(payload, str):
                    await orchestrator.on_audio_frame(payload)

            elif event == "stop":
                log.info("twilio.stream.stop")
                break

            elif event == "mark":
                # playback completion ack — could drive barge-in timing
                pass
    finally:
        if orchestrator is not None:
            await orchestrator.stop()
        structlog.contextvars.unbind_contextvars("call_sid", "stream_sid")
