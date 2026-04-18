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
- **Observability** — structlog JSON logs, request IDs, latency metrics per pipeline stage
- **Containerized** — multi-stage Dockerfile, docker-compose for local dev (Postgres + Redis)
- **CI** — ruff + pytest + docker build via GitHub Actions

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
# API:    http://localhost:8000
# Health: http://localhost:8000/health
# Docs:   http://localhost:8000/docs
```

### 4. Wire up Twilio
Point your Twilio number's **Voice → A Call Comes In** webhook to:
```
https://<your-ngrok>.ngrok.app/voice/incoming
```

### 5. Call it
Dial your Twilio number. The agent answers, listens, and books an appointment.

## Local development (without Docker)

```bash
uv sync                   # or: pip install -e ".[dev]"
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
app/
├── main.py              # FastAPI app, lifespan, health
├── config.py            # pydantic-settings
├── logging.py           # structlog JSON logger
├── routers/
│   ├── twilio.py        # /voice/incoming TwiML + /voice/stream WS
│   ├── webrtc.py        # browser WebRTC demo
│   └── sessions.py      # GET /sessions/{id} for replay
├── pipeline/
│   ├── orchestrator.py  # ties STT → LLM → TTS together per call
│   ├── stt_deepgram.py  # streaming STT client
│   ├── llm_claude.py    # Anthropic SDK, manual tool loop, caching
│   ├── tts_eleven.py    # streaming TTS client
│   └── audio.py         # μ-law/PCM conversion
├── tools/               # tool definitions + handlers
├── persistence/         # SQLAlchemy 2.0 async models + repos
└── prompts/system.md    # cached system prompt
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
