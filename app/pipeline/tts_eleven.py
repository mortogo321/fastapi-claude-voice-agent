"""Streaming TTS via ElevenLabs.

Protocol-driven (`TTSClient`) so the orchestrator can run against fakes in
tests. `ElevenLabsTTS` opens one HTTP/2 streaming connection per assistant
utterance, pushes text, and yields PCM16 16kHz mono bytes back. The
orchestrator re-encodes to μ-law for Twilio or forwards as-is for WebRTC.

`pcm_16000` skips the MP3 decode hop; `eleven_turbo_v2_5` gives
sub-300ms time-to-first-byte.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from app.config import Settings, get_settings
from app.logging import get_logger

log = get_logger(__name__)

CHUNK_BYTES = 3200  # 100ms at 16kHz PCM16 mono


class TTSClient(Protocol):
    """Surface the orchestrator needs from a text-to-speech client."""

    def synthesize_stream(self, text: str) -> AsyncIterator[bytes]: ...
    async def aclose(self) -> None: ...


class TTSError(RuntimeError):
    """Raised when the TTS provider returns a non-2xx response."""


class ElevenLabsTTS:
    BASE = "https://api.elevenlabs.io/v1"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = httpx.AsyncClient(
            base_url=self.BASE,
            timeout=httpx.Timeout(
                self._settings.elevenlabs_timeout_s,
                connect=5.0,
            ),
            http2=True,
        )

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """Stream PCM16 16kHz mono audio for a finished utterance."""
        url = f"/text-to-speech/{self._settings.elevenlabs_voice_id}/stream"
        params = {"output_format": "pcm_16000", "optimize_streaming_latency": "3"}
        body = {
            "text": text,
            "model_id": self._settings.elevenlabs_model,
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        }
        headers = {
            "xi-api-key": self._settings.elevenlabs_api_key.get_secret_value(),
            "accept": "audio/pcm",
            "content-type": "application/json",
        }

        async with self._client.stream(
            "POST", url, params=params, json=body, headers=headers
        ) as resp:
            if resp.status_code != 200:
                detail = await resp.aread()
                log.error(
                    "tts.error",
                    status=resp.status_code,
                    detail=detail[:200].decode("utf-8", "replace"),
                )
                raise TTSError(f"ElevenLabs TTS failed: {resp.status_code}")
            async for chunk in resp.aiter_bytes(chunk_size=CHUNK_BYTES):
                if chunk:
                    yield chunk

    async def aclose(self) -> None:
        await self._client.aclose()
