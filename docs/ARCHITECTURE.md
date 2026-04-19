# Architecture

## Pipeline (one inbound call)

```
┌──────────┐   TwiML    ┌──────────────┐   WS μ-law   ┌─────────────────┐
│   PSTN   │──────────▶ │   Twilio     │ ──────────▶ │ /voice/stream   │
│  caller  │            │   Voice      │ ◀────────── │ (FastAPI WS)    │
└──────────┘            └──────────────┘   audio      └────────┬────────┘
                                                               │
                       ┌───────────────────────────────────────┤
                       │                                       │
                       ▼                                       ▼
              ┌──────────────┐                        ┌────────────────┐
              │  audio.py    │                        │  audio.py      │
              │ μ-law→PCM16  │                        │  PCM16→μ-law   │
              └──────┬───────┘                        └────────▲───────┘
                     │ PCM16 16kHz                             │ PCM16 16kHz
                     ▼                                         │
              ┌──────────────┐                        ┌────────┴───────┐
              │ Deepgram WS  │ partials/finals        │  ElevenLabs    │
              │  (Nova-3)    │ ─────────┐             │  TTS stream    │
              └──────────────┘          │             │ (turbo v2.5)   │
                                        ▼             └────────▲───────┘
                                ┌────────────────┐             │
                                │  Orchestrator  │ assistant   │
                                │   per call     │ text deltas │
                                └────────┬───────┘ ────────────┘
                                         │
                                         ▼
                                ┌─────────────────┐
                                │  ClaudeAgent    │
                                │  Opus 4.7       │
                                │  + adaptive     │
                                │  + xhigh effort │
                                │  + cached sys   │
                                │  + tool loop    │
                                └────────┬────────┘
                                         │
                                         ▼
                       ┌─────────────────┴─────────────────┐
                       │                                   │
                       ▼                                   ▼
              ┌──────────────┐                    ┌──────────────────┐
              │  Tools:      │                    │  PostgreSQL      │
              │ check_avail  │                    │  call_sessions   │
              │ book_slot    │                    │  transcript_turns│
              │ send_sms     │                    │  tool_calls      │
              └──────────────┘                    └──────────────────┘
```

## Latency budget

Round-trip target: **< 1.5s** from end-of-user-speech to first TTS audio byte.

| Stage                  | Budget    | Notes                                |
|------------------------|-----------|--------------------------------------|
| Deepgram endpointing   | 300ms     | `endpointing=300` URL param          |
| LLM TTFT (cached)      | 150-250ms | Cached system + tools                |
| Tool call (in-process) | 5-50ms    | Demo tools are pure-python           |
| TTS TTFB               | 200-300ms | `optimize_streaming_latency=3`       |
| μ-law re-encode        | <5ms      | `audioop.ratecv` + `lin2ulaw`        |
| **Total**              | ~700ms    | Under budget on warm cache           |

## Why these choices

### Claude Opus 4.7 with adaptive thinking
Opus 4.7 dynamically allocates thinking tokens. For voice we want fast on
simple turns ("yes, 11am works"), deeper on complex ones ("can you check
next week and a few options on the 28th"). The `enabled` mode with a fixed
`budget_tokens` is rejected on 4.7 — adaptive is the only on-mode.

### Effort `xhigh`
Best balance for agentic voice on Opus 4.7 according to the SDK skill —
better tool selection than `high`, faster than `max`, which is reserved
for offline correctness-critical work.

### Prompt caching
Both the system prompt and the tool list are stable per call. Marking the
last block in each list with `cache_control: ephemeral` collapses TTFT on
follow-up turns from ~700ms to ~150ms and cuts per-turn cost ~10×.

### Manual tool loop (not `tool_runner`)
The SDK's `tool_runner` is convenient for batch agents but does not let us
start TTS the moment the first text block streams in or short-circuit on
barge-in. For voice latency, we run the loop ourselves.

### Real barge-in via task cancellation
Assistant playback runs as a fire-and-forget `asyncio.Task`
(`_run_playback`). The STT consumer loop stays free to receive interim
Deepgram transcripts while we speak; the moment a partial transcript
exceeds `BARGE_IN_MIN_CHARS`, the consumer calls `task.cancel()` on the
playback task. `_run_playback` catches `CancelledError`, skips the DB
persist (we never actually finished speaking), and the next final
transcript drives the next turn. No polling, no `_speaking` booleans —
the task lifecycle *is* the speaking state.

### Protocol-typed pipeline
`LLMClient`, `STTClient`, and `TTSClient` are `typing.Protocol` classes
exposing only the methods the orchestrator actually calls. Concrete
implementations (`ClaudeAgent`, `DeepgramStream`, `ElevenLabsTTS`) satisfy
them structurally. Tests inject tiny fakes through the orchestrator's
keyword-only constructor arguments — no network, no monkey-patching.

### Deepgram Nova-3 multilingual
Sub-300ms partials, native English+Thai mix in a single stream, smart-
formatted finals. Confidence scores per word would let us tune backchannel
timing in a follow-up.

### ElevenLabs turbo v2.5 + pcm_16000
`pcm_16000` skips the MP3 decode hop on our side. `turbo_v2_5` ships
sub-300ms time-to-first-byte. We re-encode to μ-law only when sending to
Twilio; WebRTC clients consume PCM16 directly.

### Postgres + SQLAlchemy 2.0 async + Alembic
Vanilla, batteries-included. Sessions, turns, and tool calls are all small
JSON-friendly rows; we don't need a vector or time-series store yet.

## Hardening already in the codebase

- **Twilio webhook HMAC validation** (`app/security.py`) on `/voice/incoming`.
  Rebuilds the URL from `PUBLIC_BASE_URL` + path so verification survives
  any reverse proxy (ngrok, Cloudflare Tunnel, load balancer). Opt-in
  bypass via `TWILIO_VALIDATE_SIGNATURE=false` for local curl testing.
- **Global concurrency cap** — `CallGate` (`app/concurrency.py`) is a
  semaphore wired into both the Twilio and WebRTC routers. The Twilio
  branch returns polite TwiML (`<Say>All our agents are busy…</Say>`)
  when full; the WebRTC branch rejects with WS close code 1013.
- **Fail-fast settings** — `Settings._require_secrets_in_production`
  blocks startup if `env=production` and any external-service secret is
  missing; `_validate_public_base_url` requires `https` in production.
- **Request-ID correlation** — `RequestContextMiddleware` mints a UUID
  per HTTP request, binds it via `structlog.contextvars.bind_contextvars`
  so every log line from any task handling that request carries the same
  `request_id`, and echoes it back as `X-Request-ID`.
- **Transport resilience** — per-provider timeout + retry settings on
  Anthropic (`max_retries=3`, `timeout=30s`), ElevenLabs (httpx timeout
  + http2), Deepgram (WS `open_timeout=5s`). Exposed as settings, not
  hard-coded.

## Production hardening (not in this POC)

- Replace `_fake_slots` with a real calendar backend.
- Sentry / OpenTelemetry instrumentation around each pipeline stage.
- Redis-backed session resume so a dropped WebSocket can reconnect mid-call.
- Distroless runtime image; AWS ECR + ECS Fargate or Fly.io deploy.
- Secrets via AWS Secrets Manager / Doppler — drop the `.env` file in prod.
