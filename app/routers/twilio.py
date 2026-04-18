"""Twilio Programmable Voice + Media Streams entrypoints.

POST /voice/incoming   → returns TwiML that opens a bidirectional Media Stream.
WS   /voice/stream     → consumes μ-law/8kHz frames, runs the agent pipeline,
                         and streams synthesized audio back to the caller.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.config import get_settings
from app.logging import get_logger
from app.pipeline.orchestrator import CallOrchestrator

router = APIRouter()
log = get_logger(__name__)


@router.post("/incoming")
async def incoming_call(request: Request) -> Response:
    """Twilio webhook for an inbound PSTN call.

    Returns TwiML that connects the call to our WebSocket endpoint via
    `<Stream>` so we receive μ-law frames and can talk back.
    """
    settings = get_settings()
    form = await request.form()
    call_sid = form.get("CallSid", "unknown")
    from_number = form.get("From", "unknown")

    log.info("twilio.incoming", call_sid=call_sid, from_number=from_number)

    ws_url = (
        settings.public_base_url.replace("https://", "wss://").replace("http://", "ws://")
        + "/voice/stream"
    )

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
      { "event": "media",  "media": { "payload": "<base64 μ-law>", "timestamp": "..." } }
      { "event": "stop",   "stop":  {...} }

    We send back:
      { "event": "media", "streamSid": "...", "media": { "payload": "<base64 μ-law>" } }
      { "event": "mark",  "streamSid": "...", "mark": { "name": "..." } }
    """
    await ws.accept()
    orchestrator: CallOrchestrator | None = None

    try:
        while True:
            raw = await ws.receive_text()
            msg: dict[str, Any] = json.loads(raw)
            event = msg.get("event")

            if event == "start":
                stream_sid = msg["start"]["streamSid"]
                params = msg["start"].get("customParameters", {})
                call_sid = params.get("callSid", stream_sid)
                from_number = params.get("from", "unknown")
                orchestrator = CallOrchestrator(
                    websocket=ws,
                    stream_sid=stream_sid,
                    call_sid=call_sid,
                    from_number=from_number,
                )
                await orchestrator.start()
                log.info(
                    "twilio.stream.start",
                    call_sid=call_sid,
                    stream_sid=stream_sid,
                )

            elif event == "media" and orchestrator is not None:
                await orchestrator.on_audio_frame(msg["media"]["payload"])

            elif event == "stop":
                log.info("twilio.stream.stop", stream_sid=msg.get("streamSid"))
                if orchestrator is not None:
                    await orchestrator.stop()
                break

            elif event == "mark":
                # playback completion ack — useful for barge-in coordination
                pass

    except WebSocketDisconnect:
        log.info("twilio.stream.disconnect")
    finally:
        if orchestrator is not None:
            await orchestrator.stop()
