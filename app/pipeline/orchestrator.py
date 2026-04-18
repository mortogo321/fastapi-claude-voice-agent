"""Per-call orchestrator.

Owns the STT WS, the Claude agent, the TTS client, and the outbound socket
to the caller (Twilio or browser). Coordinates barge-in by tracking whether
the user is currently speaking.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal

from fastapi import WebSocket

from app.logging import get_logger
from app.persistence.db import session_scope
from app.persistence.repositories import SessionRepository
from app.pipeline.audio import (
    pcm16_b64_passthrough,
    pcm16_to_ulaw_b64,
    ulaw_b64_to_pcm16,
)
from app.pipeline.llm_claude import ClaudeAgent
from app.pipeline.stt_deepgram import DeepgramStream
from app.pipeline.tts_eleven import ElevenLabsTTS
from app.tools.registry import build_default_registry

log = get_logger(__name__)

GREETING = "Hi! I can help you book an appointment. How can I help today?"

Transport = Literal["twilio", "webrtc"]


class CallOrchestrator:
    def __init__(
        self,
        websocket: WebSocket,
        stream_sid: str,
        call_sid: str,
        from_number: str,
        transport: Transport = "twilio",
    ) -> None:
        self._ws = websocket
        self._stream_sid = stream_sid
        self._call_sid = call_sid
        self._from = from_number
        self._transport: Transport = transport

        self._stt = DeepgramStream()
        self._tts = ElevenLabsTTS()
        self._registry = build_default_registry()
        self._agent = ClaudeAgent(self._registry)

        self._stt_task: asyncio.Task[None] | None = None
        self._processing = asyncio.Lock()
        self._speaking = False
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        await self._stt.connect()
        async with session_scope() as db:
            await SessionRepository(db).create(self._call_sid, self._from)
        self._stt_task = asyncio.create_task(self._consume_transcripts())
        await self._speak_and_log(GREETING, role="assistant")

    async def on_audio_frame(
        self,
        payload_b64: str,
        encoding: Literal["ulaw", "pcm16"] = "ulaw",
    ) -> None:
        pcm16 = (
            ulaw_b64_to_pcm16(payload_b64)
            if encoding == "ulaw"
            else pcm16_b64_passthrough(payload_b64)
        )
        await self._stt.send_pcm(pcm16)

    async def stop(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        if self._stt_task is not None:
            self._stt_task.cancel()
        await self._stt.close()
        await self._tts.aclose()
        async with session_scope() as db:
            await SessionRepository(db).mark_ended(self._call_sid)
        log.info("orchestrator.stopped", call_sid=self._call_sid)

    async def _consume_transcripts(self) -> None:
        async for text, is_final in self._stt.transcripts():
            if not is_final:
                if self._speaking and len(text) > 3:
                    log.info("barge_in", text=text)
                continue

            log.info("user.turn", text=text)
            async with session_scope() as db:
                await SessionRepository(db).add_turn(self._call_sid, "user", text)

            async with self._processing:
                self._agent.add_user_text(text)
                turn_started = time.perf_counter()
                turn = await self._agent.run_turn()
                latency = int((time.perf_counter() - turn_started) * 1000)

                async with session_scope() as db:
                    repo = SessionRepository(db)
                    await repo.add_turn(self._call_sid, "assistant", turn.text, latency)
                    for tc in turn.tool_calls:
                        await repo.add_tool_call(
                            self._call_sid,
                            tc["name"],
                            tc["input"],
                            tc["output"],
                            tc["latency_ms"],
                        )

                if turn.text.strip():
                    await self._speak_and_log(turn.text, role=None)

    async def _speak_and_log(self, text: str, role: str | None) -> None:
        self._speaking = True
        try:
            buffered = bytearray()
            async for pcm in self._tts.synthesize_stream(text):
                buffered.extend(pcm)
                while len(buffered) >= 3200:  # 100ms @ 16kHz PCM16
                    chunk = bytes(buffered[:3200])
                    del buffered[:3200]
                    await self._send_audio(chunk)
            if buffered:
                await self._send_audio(bytes(buffered))
            await self._send_mark("eot")
        finally:
            self._speaking = False
            if role == "assistant":
                async with session_scope() as db:
                    await SessionRepository(db).add_turn(self._call_sid, "assistant", text)

    async def _send_audio(self, pcm16: bytes) -> None:
        if self._transport == "twilio":
            payload: dict[str, Any] = {
                "event": "media",
                "streamSid": self._stream_sid,
                "media": {"payload": pcm16_to_ulaw_b64(pcm16)},
            }
        else:
            import base64

            payload = {
                "type": "audio",
                "payload": base64.b64encode(pcm16).decode("ascii"),
            }
        await self._ws.send_text(json.dumps(payload))

    async def _send_mark(self, name: str) -> None:
        if self._transport != "twilio":
            return
        await self._ws.send_text(
            json.dumps({"event": "mark", "streamSid": self._stream_sid, "mark": {"name": name}})
        )
