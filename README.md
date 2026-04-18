# fastapi-claude-voice-agent

Production-ready realtime voice AI agent built on **FastAPI**, **Anthropic Claude Opus 4.7**, **Twilio Media Streams**, **Deepgram Nova-3 STT**, and **ElevenLabs TTS**. Handles inbound PSTN calls and browser WebRTC, runs an agentic tool-use loop with prompt caching and adaptive thinking, and persists every turn to PostgreSQL.

## Features

- **PSTN voice in/out** via Twilio Programmable Voice + Media Streams (μ-law 8kHz over WebSocket)
- **Browser WebRTC** endpoint for low-latency demos
- **Streaming STT** with Deepgram Nova-3 (multilingual: English + Thai)
- **LLM** Claude Opus 4.7 with **adaptive thinking**, **prompt caching** on the system prompt and tool definitions, and a manual agentic tool-use loop tuned for sub-second voice latency
- **Streaming TTS** with ElevenLabs (eleven_turbo_v2_5)
- **Tool use** — appointment availability, booking, SMS confirmation
- **Persistence** — call sessions, transcript turns, tool calls (PostgreSQL + SQLAlchemy 2.0 async + Alembic)
- **Observability** — structlog JSON logs with per-stage latency (STT endpointing, LLM TTFT, tool call, TTS TTFB)
- **Containerized** — multi-stage Dockerfile (non-root, healthcheck), docker-compose for local dev (Postgres + Redis + one-shot Alembic migration)
- **CI** — code quality (ruff format + lint + mypy), tests with coverage, docker build — GitHub Actions

## Architecture

```
 PSTN  ──▶ Twilio ──▶ /voice/incoming (TwiML)
                       │
                       ▼
                 /voice/stream  (WebSocket, μ-law 8kHz)
                       │
   ┌───────────────────┼────────────────────────┐
   ▼                   ▼                        ▼
 Audio buf       Deepgram WS                ElevenLabs
 (μ-law⇄PCM)     (streaming STT)            (streaming TTS)
                       │                        ▲
                       ▼                        │
                  Orchestrator ──▶ Claude Opus 4.7
                       │            (adaptive thinking,
                       │             prompt caching,
                       │             tool use loop)
                       ▼
                  PostgreSQL  (sessions, turns, tool calls)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the deep dive.

## Quickstart

### 1. Prereqs
- Python 3.12+
- Docker & docker compose
- A Twilio number with Programmable Voice
- Deepgram, ElevenLabs, Anthropic API keys
- ngrok (for local Twilio webhook)

### 2. Configure
```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY,
#   TWILIO_*, DATABASE_URL, REDIS_URL, PUBLIC_BASE_URL (ngrok URL)
```

### 3. Run with Docker
```bash
docker compose up --build
# Compose order: db → migrate (alembic upgrade head, exits 0) → app
# API:    http://localhost:8000
# Health: http://localhost:8000/health
# Docs:   http://localhost:8000/docs
```

### 4. Wire up Twilio
Point your Twilio number's **Voice → A Call Comes In** webhook to (HTTP `POST`):
```
https://<your-ngrok>.ngrok.app/voice/incoming
```

### 5. Call it
Dial your Twilio number. The agent answers, listens, and books an appointment.

### Browser WebRTC demo
The same orchestrator backs `wss://<host>/webrtc/signal` for low-latency
browser demos (PCM16 16kHz mono, base64 frames). Useful for showing the
pipeline without owning a phone number.

## Local development (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"   # or: uv pip install -e ".[dev]" if you use uv
docker compose up -d db redis
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

## Tests

```bash
pytest -q
```

## Project layout

```
.
├── app/
│   ├── main.py                  # FastAPI app, lifespan, /health
│   ├── config.py                # pydantic-settings
│   ├── logging.py               # structlog JSON logger
│   ├── routers/
│   │   ├── twilio.py            # POST /voice/incoming + WS /voice/stream
│   │   ├── webrtc.py            # WS /webrtc/signal
│   │   └── sessions.py          # GET /sessions/{call_sid}
│   ├── pipeline/
│   │   ├── orchestrator.py      # per-call STT ↔ LLM ↔ TTS coordinator
│   │   ├── stt_deepgram.py      # Deepgram Nova-3 streaming WS client
│   │   ├── llm_claude.py        # Anthropic SDK, manual tool loop, caching
│   │   ├── tts_eleven.py        # ElevenLabs streaming TTS client
│   │   └── audio.py             # μ-law ⇄ PCM16 conversion
│   ├── tools/
│   │   ├── registry.py          # name → spec + handler map
│   │   ├── check_availability.py
│   │   ├── book_slot.py
│   │   └── send_confirmation.py # Twilio SMS (no-op without creds)
│   ├── persistence/
│   │   ├── db.py                # async engine + session_scope()
│   │   ├── models.py            # CallSession, TranscriptTurn, ToolCallRecord
│   │   └── repositories.py      # SessionRepository façade
│   └── prompts/system.md        # cached system prompt
├── migrations/                  # Alembic env + versions/
│   └── versions/0001_initial_schema.py
├── tests/                       # pytest (audio, tools, llm wiring, health)
├── docs/ARCHITECTURE.md         # pipeline, latency budget, design rationale
├── .github/workflows/ci.yml     # quality → test → docker build
├── Dockerfile                   # multi-stage, non-root, healthcheck
├── docker-compose.yml           # db + redis + one-shot migrate + app
├── alembic.ini
└── pyproject.toml               # deps + ruff + pytest + mypy config
```

## Design notes

### Why adaptive thinking
Opus 4.7 ships with adaptive thinking — the model decides when to reason vs. respond fast. For voice (where latency matters more than the last 5% of reasoning quality), this beats fixed `budget_tokens` (which is also no longer accepted on 4.7).

### Why prompt caching
The system prompt and the tool JSON schemas don't change between turns. We mark them with `cache_control: {"type": "ephemeral"}` so every follow-up turn in the same call reads them at the cached rate (~10× cheaper, faster TTFT).

### Why a manual tool loop
The SDK's `tool_runner` is great for batch agents, but for voice we need to:
- start streaming TTS as soon as the assistant emits text (before tool calls finish)
- log per-tool-call latency for observability
- short-circuit if the user starts speaking again (barge-in)

### Why Deepgram Nova-3
- Sub-300ms partial transcripts
- Multilingual (English + Thai in one stream)
- Confidence scores per word for adaptive backchannel timing

## License

MIT — see [LICENSE](LICENSE).
