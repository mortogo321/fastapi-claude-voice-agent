"""Audio codec helpers.

Twilio Media Streams use 8kHz μ-law (G.711). Deepgram and ElevenLabs prefer
16kHz PCM16 LE. We convert here, isolated from the network and LLM code.

Python 3.13 removed the stdlib `audioop`; we depend on `audioop-lts` on 3.13+
(see pyproject.toml). On 3.12 the stdlib `audioop` is still available.
"""

from __future__ import annotations

import audioop
import base64

ULAW_RATE = 8000
PCM_RATE = 16000


def ulaw_b64_to_pcm16(payload_b64: str) -> bytes:
    """Decode base64 μ-law 8kHz → linear PCM16 16kHz mono."""
    ulaw = base64.b64decode(payload_b64)
    pcm8k = audioop.ulaw2lin(ulaw, 2)
    pcm16k, _ = audioop.ratecv(pcm8k, 2, 1, ULAW_RATE, PCM_RATE, None)
    return pcm16k


def pcm16_to_ulaw_b64(pcm16k: bytes) -> str:
    """Encode PCM16 16kHz mono → base64 μ-law 8kHz for Twilio playback."""
    pcm8k, _ = audioop.ratecv(pcm16k, 2, 1, PCM_RATE, ULAW_RATE, None)
    ulaw = audioop.lin2ulaw(pcm8k, 2)
    return base64.b64encode(ulaw).decode("ascii")


def pcm16_b64_passthrough(payload_b64: str) -> bytes:
    """For browser WebRTC where the client already sends PCM16 16kHz mono."""
    return base64.b64decode(payload_b64)
