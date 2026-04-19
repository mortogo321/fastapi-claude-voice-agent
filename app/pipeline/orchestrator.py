"""Per-call orchestrator.

Owns the STT WS, the Claude agent, the TTS client, and the outbound socket
to the caller (Twilio or browser). Coordinates **real barge-in** by running
assistant playback as a cancellable `asyncio.Task`: when the caller starts
speaking while we're mid-utterance, the partial transcript handler cancels
that task, we stop mid-chunk, and the next final transcript drives the next
turn.

Clients are injected through `LLMClient` / `STTClient` / `TTSClient`
Protocols so tests can swap in fakes without touching the network.
"""

from __future__ import annotations

import asyncio
import base64
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
from app.pipeline.llm_claude import ClaudeAgent, LLMClient
from app.pipeline.stt_deepgram import DeepgramStream, STTClient
from app.pipeline.tts_eleven import ElevenLabsTTS, TTSClient
from app.tools.registry import build_default_registry

log = get_logger(__name__)

GREETING = "Hi! I can help you book an appointment. How can I help today?"

BARGE_IN_MIN_CHARS = 3  # "yes" / "no" are shorter; this avoids filler noise

Transport = Literal["twilio", "webrtc"]


class CallOrchestrator:
    """Owns the per-call audio pipeline."""

    def __init__(
        self,
        websocket: WebSocket,
        stream_sid: str,
        call_sid: str,
        from_number: str,
        transport: Transport = "twilio",
        *,
        llm: LLMClient | None = None,
        stt: STTClient | None = None,
        tts: TTSClient | None = None,
    ) -> None:
        self._ws = websocket
        self._stream_sid = stream_sid
        self._call_sid = call_sid
        self._from = from_number
        self._transport: Transport = transport

        self._stt: STTClient = stt or DeepgramStream()
        self._tts: TTSClient = tts or ElevenLabsTTS()
        if llm is None:
            registry = build_default_registry()
            self._llm: LLMClient = ClaudeAgent(registry)
        else:
            self._llm = llm

        self._stt_task: asyncio.Task[None] | None = None
        self._speak_task: asyncio.Task[None] | None = None
        self._processing = asyncio.Lock()
        self._stopped = asyncio.Event()

    # --- Lifecycle --------------------------------------------------------

    async def start(self) -> None:
        await self._stt.connect()
        async with session_scope() as db:
            await SessionRepository(db).create(self._call_sid, self._from)
        self._stt_task = asyncio.create_task(self._consume_transcripts(), name="stt-consumer")
        self._speak(GREETING, persist_role="assistant")

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

        self._cancel_speak()
        tasks = [t for t in (self._speak_task, self._stt_task) if t is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.debug("orchestrator.task.cleanup_error", err=str(exc))

        await self._stt.close()
        await self._tts.aclose()
        async with session_scope() as db:
            await SessionRepository(db).mark_ended(self._call_sid)
        log.info("orchestrator.stopped")

    # --- STT consumer -----------------------------------------------------

    async def _consume_transcripts(self) -> None:
        async for text, is_final in self._stt.transcripts():
            if not is_final:
                if self._is_speaking() and len(text) >= BARGE_IN_MIN_CHARS:
                    log.info("barge_in", text=text)
                    self._cancel_speak()
                continue

            await self._handle_user_turn(text)

    async def _handle_user_turn(self, text: str) -> None:
        log.info("user.turn", text=text)
        async with session_scope() as db:
            await SessionRepository(db).add_turn(self._call_sid, "user", text)

        async with self._processing:
            self._llm.add_user_text(text)
            started = time.perf_counter()
            turn = await self._llm.run_turn()
            latency_ms = int((time.perf_counter() - started) * 1000)

            async with session_scope() as db:
                repo = SessionRepository(db)
                await repo.add_turn(self._call_sid, "assistant", turn.text, latency_ms)
                for tc in turn.tool_calls:
                    await repo.add_tool_call(
                        self._call_sid,
                        tc["name"],
                        tc["input"],
                        tc["output"],
                        tc["latency_ms"],
                    )

            if turn.text.strip():
                self._speak(turn.text, persist_role=None)

    # --- TTS playback (cancellable) --------------------------------------

    def _is_speaking(self) -> bool:
        return self._speak_task is not None and not self._speak_task.done()

    def _speak(self, text: str, persist_role: str | None) -> None:
        """Kick off playback as a cancellable background task.

        Fire-and-forget so the STT consumer loop stays free to process
        interim transcripts (which is how we realise barge-in). Any prior
        in-flight playback is cancelled first. `persist_role` is only used
        by the initial greeting — LLM-turn history is written from
        `_handle_user_turn`.
        """
        prior = self._speak_task
        if prior is not None and not prior.done():
            prior.cancel()
        self._speak_task = asyncio.create_task(
            self._run_playback(text, persist_role), name="tts-playback"
        )

    def _cancel_speak(self) -> None:
        task = self._speak_task
        if task is None or task.done():
            return
        task.cancel()

    async def _run_playback(self, text: str, persist_role: str | None) -> None:
        buffered = bytearray()
        cancelled = False
        try:
            async for pcm in self._tts.synthesize_stream(text):
                buffered.extend(pcm)
                while len(buffered) >= 3200:  # 100ms @ 16kHz PCM16
                    chunk = bytes(buffered[:3200])
                    del buffered[:3200]
                    await self._send_audio(chunk)
            if buffered:
                await self._send_audio(bytes(buffered))
            await self._send_mark("eot")
        except asyncio.CancelledError:
            cancelled = True
            log.info("tts.cancelled")
        finally:
            if persist_role == "assistant" and not cancelled:
                async with session_scope() as db:
                    await SessionRepository(db).add_turn(self._call_sid, "assistant", text)

    # --- Transport wire format -------------------------------------------

    async def _send_audio(self, pcm16: bytes) -> None:
        if self._transport == "twilio":
            payload: dict[str, Any] = {
                "event": "media",
                "streamSid": self._stream_sid,
                "media": {"payload": pcm16_to_ulaw_b64(pcm16)},
            }
        else:
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
