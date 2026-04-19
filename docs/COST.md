# Cost of ownership

What it actually costs to **run** this voice agent in production. One-time
build cost is intentionally excluded — this doc answers the question
"if I owned this system, what would I pay every month?"

All figures are USD, based on public pricing as of late 2025 / early 2026.
Real invoices land ±15% depending on contract tier, region, and call mix.
Numbers here are a planning tool, not a quote.

---

## TL;DR

| Monthly call volume (3-min avg call) | Total / month | Per-call all-in |
|---|---|---|
| 100 calls (~3/day, pilot)     | **~$150**    | $1.46 |
| 1,000 calls (~33/day)         | **~$380**    | $0.38 |
| 10,000 calls (~330/day)       | **~$2,800**  | $0.28 |
| 100,000 calls (~3.3k/day)     | **~$26,600** | $0.27 |

Two drivers dominate:

1. **Call volume** — 90% of the bill above ~5k calls/mo is variable cost.
2. **Model + voice choice** — Opus 4.7 + ElevenLabs premium is the
   top tier; swapping either can cut 30-50% off per-call cost.

The fixed-infrastructure floor is **~$60-200/month** no matter the
traffic.

---

## Cost breakdown

The monthly bill has three layers:

```
┌───────────────────────────────────────────────┐
│  Variable — per call                          │
│  (Twilio minutes, STT, LLM, TTS, SMS)         │  ← scales with usage
├───────────────────────────────────────────────┤
│  Fixed — per month                            │
│  (app host, database, cache, phone number,    │  ← flat baseline
│   observability)                              │
├───────────────────────────────────────────────┤
│  Operating overhead                           │
│  (maintenance, on-call, compliance)           │  ← optional / discretionary
└───────────────────────────────────────────────┘
```

> Glossary for the variable line above: **STT** = speech-to-text,
> **LLM** = large language model, **TTS** = text-to-speech,
> **SMS** = short message service (text message).

### 1. Per-call variable cost

Assumptions for a representative call:
- 3 minutes of inbound audio
- 1 minute of assistant TTS output (~900 characters)
- 8 conversational turns
- System prompt + tool schemas cached after the first turn
- One SMS confirmation at the end

| Service | Rate | Per 3-min call |
|---|---|---|
| Twilio PSTN (public switched telephone network) inbound (US local number) | ~$0.0085 / min | ~$0.026 |
| Deepgram Nova-3 streaming STT (multilingual) | ~$0.0077 / min | ~$0.023 |
| ElevenLabs Turbo v2.5 TTS | ~$0.15 / 1k chars | ~$0.135 |
| Anthropic Claude Opus 4.7 (with prompt caching) | $5 / $25 per 1M in/out | ~$0.06 |
| Twilio SMS confirmation (US) | ~$0.008 / message | ~$0.008 |
| **Total** | | **~$0.26 / call** |

Notes on each line:

- **Twilio** charges per-minute from the carrier side — Media Streams
  itself is free but the voice leg is billed normally. Toll-free and
  international numbers cost 2-5× more per minute; an incoming call from
  Thailand or the UK is materially more expensive than a US local one.
- **Deepgram** Nova-3 multilingual streaming is ~$0.0077/min at current
  public rates. Committed-volume enterprise contracts drop this 30-50%.
- **ElevenLabs** dominates the bill. The price-per-character depends on
  the tier (Creator: $0.30/1k; Pro: $0.24/1k; Scale: $0.12/1k;
  Business: ~$0.10/1k and below). A different TTS vendor (Deepgram Aura,
  Azure Neural, Google Chirp3-HD) cuts this by 50-90% at the cost of
  some voice quality.
- **Anthropic Claude Opus 4.7** is the premium tier. Prompt caching
  makes follow-up turns ~10× cheaper on the cached prefix, which is
  why per-call LLM cost stays under $0.10 even with adaptive thinking
  enabled. Switching to **Sonnet 4.6** ($3/$15 per 1M) cuts this line
  to ~$0.015. Haiku 4.5 cuts it to ~$0.005.

### 2. Fixed monthly infrastructure

| Component | Typical small deployment | Production HA (high-availability) |
|---|---|---|
| Application host (FastAPI + ASGI, async server gateway interface) | $30-60  (2 vCPU / 4 GB)  | $150-300 (HA + reserved) |
| PostgreSQL (sessions, turns, tools)    | $20-50  (small managed)  | $150-400 (multi-AZ, availability zone) |
| Redis (concurrency gate, cache)        | $10-25                   | $40-100 |
| Twilio phone number (US local)         | $1.15                    | $1.15 |
| Observability (logs, metrics, traces)  | $0-50   (free tiers OK)  | $100-300 |
| Domain + TLS + CDN (content delivery network) / WAF (web application firewall) | $0-20 | $20-80 |
| Secrets manager                        | $0-15                    | $20-40 |
| **Baseline / month** | **~$60-200** | **~$480-1,200** |

Toll-free numbers, international DIDs (direct inward dialing numbers), and multi-region deployments add
on top. A single US-market deployment on a starter cloud tier fits in
the ~$100/mo column; regulated industries or global coverage quickly
push into the four-figure floor.

### 3. Operating overhead (optional)

| Item | Typical cost | Notes |
|---|---|---|
| Maintenance engineer (0.1-0.2 FTE, full-time-equivalent) | $1.5k-4k/mo (SEA, Southeast Asia rates) | Prompt tuning, dependency updates, small bugs |
| 24/7 on-call rotation                | +$100-300/mo           | PagerDuty tier + rotation policy |
| Compliance — TCPA (Telephone Consumer Protection Act), STIR/SHAKEN (call-authenticity standards), 10DLC (10-digit long-code SMS registration) | ~$200-500 one-time + ~$15/mo | US only; country-specific rules apply |
| Call recording storage               | ~$5-30/mo              | S3/equivalent; scales with retention window |

These are **real** costs but discretionary — you can run without a
maintenance retainer if you're willing to accept slower response to
drift or incidents.

---

## Scenarios

### Pilot — internal beta, ~100 calls/month

For a proof-of-concept or internal dogfood:

| | |
|---|---|
| Variable (100 × $0.26)            | $26 |
| Fixed infra (starter tier)         | $120 |
| **Monthly total**                  | **~$146** |
| Fully loaded per call              | $1.46 |

At this volume **the fixed infra dominates** — cost per call is
dictated by the baseline, not the per-call rates. Efficiency tuning
here has negligible return; focus on getting to meaningful volume.

### Small production — 1,000 calls/month

A small clinic, SMB (small and medium business) receptionist, or a single-site booking agent:

| | |
|---|---|
| Variable (1,000 × $0.26)           | $260 |
| Fixed infra (starter tier)         | $120 |
| **Monthly total**                  | **~$380** |
| Fully loaded per call              | $0.38 |

This is typically where **call minutes start mattering**. If average
calls stretch to 5 minutes, recompute: Twilio + STT + TTS all scale
roughly linearly.

### Medium production — 10,000 calls/month

A multi-location business, a busy support line, or a mid-market
outbound dialer:

| | |
|---|---|
| Variable (10,000 × $0.26)          | $2,600 |
| Fixed infra (mid tier: HA DB, Datadog starter) | $200 |
| Light maintenance retainer         | $1,500 |
| **Monthly total**                  | **~$4,300** |
| Fully loaded per call              | $0.43 |

At this volume **commit-based pricing** with each vendor becomes
available. A 6-month commit with Anthropic, Deepgram, and ElevenLabs
typically saves 20-40%, pushing the per-call variable cost to ~$0.20
and the total toward ~$3,500/mo.

### Large production — 100,000 calls/month

Enterprise / multi-tenant / regional coverage:

| | |
|---|---|
| Variable (100,000 × $0.26)         | $26,000 |
| Fixed infra (production HA)        | $800 |
| Maintenance (0.2 FTE) + on-call    | $4,000 |
| **Monthly total (list pricing)**   | **~$30,800** |
| With enterprise commits (−30%)     | **~$22,000** |
| Fully loaded per call (committed)  | $0.22 |

At this scale the system becomes vendor-commitment-bound: the
difference between list and committed rates is **six figures per
year**. Engagement with each vendor's sales team is worth the effort.

---

## Cost levers (in descending order of impact)

Knobs you can turn if the quote above is too high:

| Lever | How to pull it | Typical saving |
|---|---|---|
| LLM tier | Opus 4.7 → Sonnet 4.6 → Haiku 4.5 | 70% / 95% off LLM line |
| TTS vendor | ElevenLabs → Deepgram Aura / Azure / Google | 50-90% off TTS line |
| Vendor commits | Month-to-month → 6-12 mo annual commit | 20-40% across all API lines |
| Call duration | Prompt tuning to shorten calls | Linear with minutes saved |
| Barge-in discipline | Cut wasted TTS from interrupted responses | 5-15% off TTS |
| Caching hit rate | Keep system prompt frozen; don't inject timestamps | Up to 10× on LLM input cost |
| Regional telephony | Host close to callers; avoid international legs | 50-80% off Twilio line |
| Self-host DB/Redis | Managed → self-managed on existing VMs | $30-80/mo |

The biggest single lever is **the LLM**. If Opus-level quality is not
required for the task, downgrading pays for the entire rest of the
stack several times over.

---

## What this estimate does NOT cover

Be explicit with stakeholders that the numbers above exclude:

- **Development / build cost** — covered separately.
- **Professional services** — vendor onboarding, integration work
  (CRM — customer relationship management — calendar, knowledge base),
  custom voice training.
- **Data-residency / compliance-driven infra** — HIPAA (Health Insurance
  Portability and Accountability Act) BAAs (business associate
  agreements), SOC 2 (Service Organization Control 2) auditing,
  geographic isolation requirements may force premium tiers on every
  component.
- **Call recording transcription for analytics** — a second STT pass
  on stored audio roughly doubles the STT line item.
- **Customer-facing dashboard / admin UI (user interface)** — separate frontend project.
- **Translation or i18n (internationalization) prompt maintenance** —
  per-language prompt engineering and regression testing.
- **Insurance, legal review, and VAT (value-added tax)** — country-specific.

---

## How to adapt this estimate to a specific client

The fastest path to a real number:

1. Pin **expected call volume** (calls/month) and **average duration**.
2. Pin the **LLM tier** (Opus vs. Sonnet vs. Haiku) — this is the
   biggest cost knob.
3. Pin the **TTS vendor/tier** — the second biggest.
4. Pick **US local / toll-free / international** telephony mix.
5. Decide **maintenance retainer** (bundled vs. ad-hoc).

With those five inputs the monthly run-rate is deterministic to within
~15%. Anything tighter than that requires a paid pilot and one real
month of invoices.
