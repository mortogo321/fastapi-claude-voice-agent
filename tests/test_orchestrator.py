"""Integration test for `CallOrchestrator` using injected fake clients.

Verifies:
  * greeting is spoken on start,
  * a final transcript drives exactly one LLM turn,
  * barge-in: a long enough partial transcript during TTS cancels the
    current playback task.

No real network or database: `session_scope` is monkey-patched to a no-op
and each Protocol is satisfied by a small fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.pipeline import orchestrator as orch_mod
from app.pipeline.llm_claude import StreamedChunk, TurnResult
from app.pipeline.orchestrator import BARGE_IN_MIN_CHARS, CallOrchestrator

# --- Fakes ----------------------------------------------------------------


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


class FakeSTT:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[tuple[str, bool]] = asyncio.Queue()
        self._closed = asyncio.Event()
        self.sent_pcm: list[bytes] = []

    async def connect(self) -> None:
        return None

    async def send_pcm(self, pcm16: bytes) -> None:
        self.sent_pcm.append(pcm16)

    def transcripts(self) -> AsyncIterator[tuple[str, bool]]:
        async def _iter() -> AsyncIterator[tuple[str, bool]]:
            while not self._closed.is_set():
                try:
                    yield await asyncio.wait_for(self._queue.get(), timeout=0.05)
                except TimeoutError:
                    if self._closed.is_set():
                        break

        return _iter()

    async def close(self) -> None:
        self._closed.set()

    async def emit(self, text: str, is_final: bool) -> None:
        await self._queue.put((text, is_final))


class FakeTTS:
    """Streams a bounded number of 3200-byte PCM chunks with short awaits.

    `chunks` controls duration so tests can trigger barge-in mid-stream.
    """

    def __init__(self, chunks: int = 50, chunk_sleep: float = 0.01) -> None:
        self._chunks = chunks
        self._sleep = chunk_sleep
        self.calls: list[str] = []

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        self.calls.append(text)
        for _ in range(self._chunks):
            await asyncio.sleep(self._sleep)
            yield b"\x00" * 3200

    async def aclose(self) -> None:
        return None


class FakeLLM:
    def __init__(self, reply: str = "ok") -> None:
        self._reply = reply
        self.user_texts: list[str] = []
        self.turns = 0

    def add_user_text(self, text: str) -> None:
        self.user_texts.append(text)

    async def run_turn(self, on_text_chunk: Any | None = None) -> TurnResult:
        self.turns += 1
        if on_text_chunk is not None:
            await on_text_chunk(StreamedChunk(text_delta=self._reply))
            await on_text_chunk(StreamedChunk(is_final=True))
        return TurnResult(text=self._reply)


# --- Fixtures -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_session_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextlib.asynccontextmanager
    async def _noop() -> AsyncIterator[object]:
        class _Repo:
            async def create(self, *_a: Any, **_kw: Any) -> None:
                return None

            async def mark_ended(self, *_a: Any, **_kw: Any) -> None:
                return None

            async def add_turn(self, *_a: Any, **_kw: Any) -> None:
                return None

            async def add_tool_call(self, *_a: Any, **_kw: Any) -> None:
                return None

        yield _Repo()

    monkeypatch.setattr(orch_mod, "session_scope", _noop)

    class _RepoFactory:
        def __init__(self, _db: Any) -> None:
            self._db = _db

        def __getattr__(self, _name: str) -> Any:
            return self._db.__getattribute__(_name)

    monkeypatch.setattr(orch_mod, "SessionRepository", _RepoFactory)


def _make_orchestrator(
    llm: FakeLLM, stt: FakeSTT, tts: FakeTTS
) -> tuple[CallOrchestrator, FakeWebSocket]:
    ws = FakeWebSocket()
    orch = CallOrchestrator(
        websocket=ws,  # type: ignore[arg-type]
        stream_sid="stream-1",
        call_sid="call-1",
        from_number="+10000000000",
        transport="twilio",
        llm=llm,
        stt=stt,
        tts=tts,
    )
    return orch, ws


# --- Tests ----------------------------------------------------------------


async def test_greeting_is_spoken_on_start() -> None:
    llm, stt, tts = FakeLLM(), FakeSTT(), FakeTTS(chunks=2)
    orch, ws = _make_orchestrator(llm, stt, tts)

    await orch.start()
    # Let the background playback task finish.
    for _ in range(50):
        if tts.calls and not orch._is_speaking():
            break
        await asyncio.sleep(0.01)
    await orch.stop()

    assert tts.calls, "expected greeting to be synthesized"
    assert any(m.get("event") == "media" for m in ws.sent)
    assert any(m.get("event") == "mark" for m in ws.sent)


async def test_final_transcript_drives_one_llm_turn() -> None:
    llm, stt, tts = FakeLLM(reply="sure thing"), FakeSTT(), FakeTTS(chunks=2)
    orch, _ws = _make_orchestrator(llm, stt, tts)

    await orch.start()
    await stt.emit("book a slot tomorrow", is_final=True)

    for _ in range(100):
        await asyncio.sleep(0.01)
        if llm.turns == 1 and "sure thing" in tts.calls:
            break
    await orch.stop()

    assert llm.turns == 1
    assert llm.user_texts == ["book a slot tomorrow"]
    assert "sure thing" in tts.calls


async def test_partial_transcript_cancels_in_flight_tts() -> None:
    llm = FakeLLM(reply="here is a long assistant reply to interrupt")
    stt = FakeSTT()
    # Long playback so we have time to cut in.
    tts = FakeTTS(chunks=200, chunk_sleep=0.005)
    orch, _ws = _make_orchestrator(llm, stt, tts)

    await orch.start()
    # Kick off an assistant utterance.
    await stt.emit("tell me more", is_final=True)
    # Wait until the assistant playback task is actively speaking.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if orch._is_speaking() and tts.calls and tts.calls[-1].startswith("here"):
            break
    assert orch._is_speaking(), "expected TTS task to be active"

    # Simulate user starting to speak — long enough to trigger barge-in.
    interrupt = "x" * (BARGE_IN_MIN_CHARS + 1)
    await stt.emit(interrupt, is_final=False)

    # Give the consumer a beat to process the partial and cancel.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if not orch._is_speaking():
            break

    assert not orch._is_speaking(), "playback should have been cancelled"
    await orch.stop()
