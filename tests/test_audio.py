from __future__ import annotations

import base64

from app.pipeline.audio import pcm16_to_ulaw_b64, ulaw_b64_to_pcm16


def test_ulaw_pcm_roundtrip_quadruples_byte_count():
    # 160 bytes μ-law 8kHz → ~640 bytes PCM16 16kHz (1B → 2B per sample, 8k→16k).
    # ratecv loses 1-2 samples on the first call without prior state, so we
    # check a small tolerance band around the expected size.
    silence_ulaw = b"\xff" * 160
    pcm16 = ulaw_b64_to_pcm16(base64.b64encode(silence_ulaw).decode())
    assert 630 <= len(pcm16) <= 642


def test_pcm16_to_ulaw_halves_byte_rate():
    # 6400 bytes PCM16 16kHz = 200ms; μ-law 8kHz of 200ms ≈ 1600 bytes ±2.
    pcm16 = b"\x00\x00" * 3200
    ulaw_b64 = pcm16_to_ulaw_b64(pcm16)
    ulaw = base64.b64decode(ulaw_b64)
    assert 1595 <= len(ulaw) <= 1602
