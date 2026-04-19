"""Streaming STT via Deepgram WebSocket.

Protocol-driven (`STTClient`) so the orchestrator can be tested with fakes
without monkey-patching. `DeepgramStream` opens one WS per call, pushes
PCM16 16kHz mono frames in, and yields `(text, is_final)` tuples as they
arrive. Partial transcripts let the orchestrator decide when to interrupt
the assistant for barge-in.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import websockets
from websockets.asyncio.client import ClientConnection

from app.config import Settings, get_settings
from app.logging import get_logger

log = get_logger(__name__)

DEEPGRAM_WS = (
    "wss://api.deepgram.com/v1/listen"
    "?model={model}"
    "&language={language}"
    "&encoding=linear16"
    "&sample_rate=16000"
    "&channels=1"
    "&interim_results=true"
    "&smart_format=true"
    "&endpointing={endpointing}"
)

CONNECT_TIMEOUT_S = 5.0


class STTClient(Protocol):
    """Surface the orchestrator needs from a speech-to-text client."""

    async def connect(self) -> None: ...
    async def send_pcm(self, pcm16: bytes) -> None: ...
    def transcripts(self) -> AsyncIterator[tuple[str, bool]]: ...
    async def close(self) -> None: ...


class DeepgramStream:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._ws: ClientConnection | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        self._closed = asyncio.Event()

    async def connect(self) -> None:
        url = DEEPGRAM_WS.format(
            model=self._settings.deepgram_model,
            language=self._settings.deepgram_language,
            endpointing=self._settings.deepgram_endpointing_ms,
        )
        api_key = self._settings.deepgram_api_key.get_secret_value()
        self._ws = await asyncio.wait_for(
            websockets.connect(
                url,
                additional_headers={"Authorization": f"Token {api_key}"},
                max_size=4 * 1024 * 1024,
                open_timeout=CONNECT_TIMEOUT_S,
            ),
            timeout=CONNECT_TIMEOUT_S,
        )
        self._recv_task = asyncio.create_task(self._reader(), name="deepgram-reader")
        log.info("deepgram.connected", endpointing_ms=self._settings.deepgram_endpointing_ms)

    async def send_pcm(self, pcm16: bytes) -> None:
        if self._ws is None:
            raise RuntimeError("DeepgramStream not connected")
        await self._ws.send(pcm16)

    async def transcripts(self) -> AsyncIterator[tuple[str, bool]]:
        """Yields (text, is_final) tuples until the stream closes."""
        while not self._closed.is_set():
            try:
                yield await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                if self._closed.is_set():
                    break

    async def close(self) -> None:
        self._closed.set()
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
            except Exception as exc:
                log.debug("deepgram.close.send_failed", err=str(exc))
            try:
                await self._ws.close()
            except Exception as exc:
                log.debug("deepgram.close.err", err=str(exc))
            self._ws = None
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.debug("deepgram.recv_task.cleanup_error", err=str(exc))

    async def _reader(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                msg: dict[str, Any] = json.loads(raw)
                if msg.get("type") != "Results":
                    continue
                alt = msg.get("channel", {}).get("alternatives", [{}])[0]
                text = (alt.get("transcript") or "").strip()
                is_final = bool(msg.get("is_final"))
                if text:
                    await self._queue.put((text, is_final))
        except websockets.ConnectionClosed:
            pass
        finally:
            self._closed.set()
