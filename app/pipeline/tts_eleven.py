"""Streaming TTS via ElevenLabs.

We open one HTTP/2 streaming connection per assistant utterance, push text
chunks, and yield PCM16 16kHz mono audio bytes back. The orchestrator
re-encodes to μ-law for Twilio or forwards as-is for WebRTC.

We use the `pcm_16000` output format (no MP3 decode hop) and the
`eleven_turbo_v2_5` model (sub-300ms time-to-first-byte).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.config import get_settings
from app.logging import get_logger

log = get_logger(__name__)


class ElevenLabsTTS:
    BASE = "https://api.elevenlabs.io/v1"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client = httpx.AsyncClient(
            base_url=self.BASE,
            timeout=httpx.Timeout(30.0, connect=5.0),
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
            "xi-api-key": self._settings.elevenlabs_api_key,
            "accept": "audio/pcm",
            "content-type": "application/json",
        }

        async with self._client.stream(
            "POST", url, params=params, json=body, headers=headers
        ) as resp:
            if resp.status_code != 200:
                detail = await resp.aread()
                log.error("tts.error", status=resp.status_code, detail=detail[:200])
                raise RuntimeError(f"ElevenLabs TTS failed: {resp.status_code}")
            async for chunk in resp.aiter_bytes(chunk_size=3200):
                if chunk:
                    yield chunk

    async def aclose(self) -> None:
        await self._client.aclose()


def safe_json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
