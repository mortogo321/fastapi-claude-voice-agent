"""Claude Opus 5 client for the voice agent.

Design choices, all from the claude-api skill mandates:

- Model: `claude-opus-5` (never downgrade unless explicitly asked).
- `thinking={"type": "adaptive"}` — thinking is ON by default on Opus 5, but
  we still set this explicitly so `display="summarized"` takes effect;
  `enabled` + `budget_tokens` returns 400 on this model.
- `display="summarized"` so the user sees thinking progress instead of a
  silent pause (default on Opus 5 is `omitted`).
- `effort="xhigh"` inside `output_config` — best balance for agentic voice
  on Opus 5 (`max` is reserved for offline correctness-critical work). We
  never disable thinking here, so the "disabled thinking + xhigh/max effort
  returns 400" pitfall on Opus 5 does not apply.
- `anthropic_max_tokens` (see `app/config.py`) caps thinking + spoken text
  *together* on Opus 5 — it was bumped from 1024 to 4096 so adaptive
  thinking at `xhigh` effort can't crowd out the actual reply.
- **Prompt caching** on:
    1. The system prompt (loaded from `app/prompts/system.md`).
    2. The tool JSON schema list.
  Both are stable across every turn in a call, so we mark the last item in
  each list with `cache_control={"type": "ephemeral"}`. This collapses TTFT
  on follow-up turns from ~700ms to ~150ms.
- Manual agentic loop (not the SDK's `tool_runner`) so we can:
    * begin streaming TTS as soon as the first text block appears,
    * record per-tool-call latency,
    * abort cleanly on barge-in.
- `stop_reason == "refusal"` is handled as a graceful decline: no tool
  results to feed back, and we fall back to a fixed apology line if the
  model produced no visible text (see `_REFUSAL_FALLBACK_TEXT` below).
- Streaming with `client.messages.stream(...)` — required for any voice
  request that may run long; we use `.get_final_message()` to collect the
  full response before resuming the loop.
- Transport resilience comes from the SDK's `max_retries` (exponential
  backoff on `APIConnectionError`, 408, 409, 429, 5xx) and per-request
  `timeout`. We surface both as settings rather than hard-coding them.
- We deliberately do NOT adopt the `fallbacks` (model-fallbacks) beta —
  out of scope for this migration; a refusal is surfaced to the caller
  instead of being silently retried on another model.

NEVER set `temperature`, `top_p`, `top_k`, or `budget_tokens` here — Opus 5
removed them and will 400.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from anthropic import AsyncAnthropic

from app.config import Settings, get_settings
from app.logging import get_logger
from app.tools.registry import ToolRegistry

log = get_logger(__name__)

SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system.md"

# Spoken fallback when Claude refuses (stop_reason == "refusal") and produced
# no visible text to speak — keeps the call graceful instead of going silent.
_REFUSAL_FALLBACK_TEXT = "Sorry, I can't help with that. Is there something else I can do for you?"


@dataclass(slots=True)
class TurnResult:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    latency_ms: int = 0


@dataclass(slots=True)
class StreamedChunk:
    """Emitted while the model is producing the assistant turn."""

    text_delta: str = ""
    is_final: bool = False


class LLMClient(Protocol):
    """Minimal surface the orchestrator needs from a language model.

    Implemented by `ClaudeAgent`; tests swap in `FakeLLMClient`.
    """

    async def run_turn(
        self,
        on_text_chunk: Callable[[StreamedChunk], Awaitable[None]] | None = None,
    ) -> TurnResult: ...

    def add_user_text(self, text: str) -> None: ...


class ClaudeAgent:
    """One ClaudeAgent per call. Holds the running message history."""

    def __init__(
        self,
        registry: ToolRegistry,
        settings: Settings | None = None,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or AsyncAnthropic(
            api_key=self._settings.anthropic_api_key.get_secret_value(),
            timeout=self._settings.anthropic_timeout_s,
            max_retries=self._settings.anthropic_max_retries,
        )
        self._model = self._settings.anthropic_model
        self._max_tokens = self._settings.anthropic_max_tokens
        self._registry = registry
        self._messages: list[dict[str, Any]] = []
        self._system = self._build_system_blocks()
        self._tools = self._build_tool_blocks()

    @staticmethod
    def _build_system_blocks() -> list[dict[str, Any]]:
        prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        return [
            {
                "type": "text",
                "text": prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _build_tool_blocks(self) -> list[dict[str, Any]]:
        tools = self._registry.tool_specs()
        if tools:
            tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools

    def add_user_text(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    async def run_turn(
        self,
        on_text_chunk: Callable[[StreamedChunk], Awaitable[None]] | None = None,
    ) -> TurnResult:
        """Run the agentic loop for one user turn.

        Loops `messages.stream()` → tool execution → next stream until the
        model returns a stop_reason that isn't `tool_use`.
        """
        started = time.perf_counter()
        result = TurnResult(text="")

        while True:
            assistant_text, content_blocks, usage, stop_reason = await self._stream_once(
                on_text_chunk
            )
            result.text += assistant_text
            result.input_tokens += usage["input_tokens"]
            result.output_tokens += usage["output_tokens"]
            result.cache_read_tokens += usage["cache_read_input_tokens"]
            result.cache_creation_tokens += usage["cache_creation_input_tokens"]

            self._messages.append({"role": "assistant", "content": content_blocks})

            if stop_reason == "refusal":
                # Safety classifiers declined this turn — nothing to feed back
                # to the model, so treat it as a graceful "can't help" reply
                # rather than continuing the tool loop.
                log.warning("llm.refusal", text_len=len(result.text))
                if not result.text.strip():
                    result.text = _REFUSAL_FALLBACK_TEXT
                break

            tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]
            if not tool_uses:
                break

            tool_results: list[dict[str, Any]] = []
            for tu in tool_uses:
                tool_started = time.perf_counter()
                output = await self._registry.execute(tu["name"], tu.get("input", {}))
                tool_latency_ms = int((time.perf_counter() - tool_started) * 1000)
                result.tool_calls.append(
                    {
                        "name": tu["name"],
                        "input": tu.get("input", {}),
                        "output": output,
                        "latency_ms": tool_latency_ms,
                    }
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(output, ensure_ascii=False, default=str),
                            }
                        ],
                    }
                )

            self._messages.append({"role": "user", "content": tool_results})

        result.latency_ms = int((time.perf_counter() - started) * 1000)
        log.info(
            "llm.turn",
            text_len=len(result.text),
            tool_calls=len(result.tool_calls),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_read=result.cache_read_tokens,
            cache_creation=result.cache_creation_tokens,
            latency_ms=result.latency_ms,
        )
        return result

    async def _stream_once(
        self,
        on_text_chunk: Callable[[StreamedChunk], Awaitable[None]] | None,
    ) -> tuple[str, list[dict[str, Any]], dict[str, int], str | None]:
        """Stream one model response. Returns (text, content_blocks, usage, stop_reason)."""
        text_buf: list[str] = []

        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._system,  # type: ignore[arg-type]
            tools=self._tools,  # type: ignore[arg-type]
            messages=self._messages,  # type: ignore[arg-type]
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "xhigh"},
        ) as stream:
            async for chunk in self._iter_text_deltas(stream):
                text_buf.append(chunk)
                if on_text_chunk is not None:
                    await on_text_chunk(StreamedChunk(text_delta=chunk))

            final = await stream.get_final_message()

        if on_text_chunk is not None:
            await on_text_chunk(StreamedChunk(is_final=True))

        content_blocks = [b.model_dump() for b in final.content]
        usage = {
            "input_tokens": final.usage.input_tokens,
            "output_tokens": final.usage.output_tokens,
            "cache_read_input_tokens": getattr(final.usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(final.usage, "cache_creation_input_tokens", 0)
            or 0,
        }
        return "".join(text_buf), content_blocks, usage, final.stop_reason

    @staticmethod
    async def _iter_text_deltas(stream: Any) -> AsyncIterator[str]:
        async for event in stream:
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                yield event.delta.text
