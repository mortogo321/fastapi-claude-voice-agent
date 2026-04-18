"""Streaming STT via Deepgram WebSocket.

We open one WS per call, push PCM16 16kHz mono frames in, and yield
`(text, is_final)` tuples as they arrive. Partial transcripts let the
orchestrator decide when to interrupt the assistant for barge-in.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import websockets

from app.config import get_settings
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
    "&endpointing=300"
)


class DeepgramStream:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._ws: Any | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        self._closed = asyncio.Event()

    async def connect(self) -> None:
        url = DEEPGRAM_WS.format(
            model=self._settings.deepgram_model,
            language=self._settings.deepgram_language,
        )
        self._ws = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Token {self._settings.deepgram_api_key}"},
            max_size=4 * 1024 * 1024,
        )
        self._recv_task = asyncio.create_task(self._reader())
        log.info("deepgram.connected")

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
            await self._ws.close()
            self._ws = None
        if self._recv_task is not None:
            self._recv_task.cancel()

    async def _reader(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") != "Results":
                    continue
                alt = msg.get("channel", {}).get("alternatives", [{}])[0]
                text = alt.get("transcript", "").strip()
                is_final = bool(msg.get("is_final"))
                if text:
                    await self._queue.put((text, is_final))
        except websockets.ConnectionClosed:
            pass
        finally:
            self._closed.set()
