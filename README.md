# PulseCare Real-Time Voice Desk (LiveKit + Groq + FastAPI)

**Speak live to an AI clinic receptionist, watch the transcript and per-turn latency stream in real time, verify your details before booking, and see the clinic schedule update live.**

![PulseCare Live Voice Call & Booking Demo](docs/demo.gif)

---

## Three Ways to Run Voice

1. **Primary Real-Time Voice (LiveKit + Groq + Cartesia/Deepgram)** — Full WebRTC audio pipeline: Silero VAD + Groq `whisper-large-v3-turbo` STT + Groq `llama-3.3-70b-versatile` LLM + streaming Cartesia `sonic-2` / Deepgram `aura-2` TTS. Requires `.env` API keys.
2. **Keyless Real-Voice Mode (Browser Web Speech API — Zero API Keys)** — Click **Talk Now (Keyless Browser Voice)** in Chrome/Edge to speak directly using `SpeechRecognition` (mic input) and `speechSynthesis` (spoken output) wired to `POST /api/voice/turn` + real SQLite tools. Lower recognition/voice quality and not supported in every browser (Firefox falls back to the built-in turn text box), but lets any reviewer talk to the agent with zero setup.
3. **Keyless Fallback: Replay Simulation** — Click **Keyless Fallback: Replay Simulation** in the UI to replay a scripted multi-turn walkthrough against the live FastAPI + SQLite backend without a microphone or API keys.

---

## Voice Edge Cases & Safety Guards

| Edge Case | How It Is Handled |
|---|---|
| **Verify Before Booking (`Shall I confirm?`)** | Both [agent/main.py](file:///C:/Users/nkuma/.gemini/antigravity/scratch/voice-clinic-agent/agent/main.py) (`confirmed: bool = False` tool guard on `book_appointment` / `reschedule_appointment` / `cancel_appointment`) and [backend/voice_engine.py](file:///C:/Users/nkuma/.gemini/antigravity/scratch/voice-clinic-agent/backend/voice_engine.py) (`awaiting_booking_confirmation`) refuse to write to SQLite on the first pass. Maya reads back the doctor, specialty, date, time, patient name, and phone number and asks **"Shall I confirm?"**, committing only after an affirmative response. |
| **Confirming Names, Phones & Dates** | Names, grouped phone digits (e.g. `5 5 5, 0 1 8 4`), and resolved calendar dates (`today` / `tomorrow` / weekdays -> `YYYY-MM-DD`) are echoed back explicitly before booking; inline corrections (e.g., *"No, my name is Jordan Ray"*) update the pending draft without writing to the database. |
| **Barge-In (Caller Interrupts Agent)** | **LiveKit:** `AgentSession` enables `allow_interruptions=True`, `min_interruption_duration=0.4`, `min_interruption_words=1`, `false_interruption_timeout=2.0`, and `resume_false_interruption=True`, emitting `barge_in` telemetry on `speaking -> listening`. **Browser Voice:** `SpeechRecognition.onspeechstart` / interim speech immediately cancels `window.speechSynthesis.cancel()` and switches back to `LISTENING`. |
| **Silence Handling** | **LiveKit:** `user_away_timeout=12.0` triggers a gentle check-in via `user_state_changed`. **Browser Voice:** a 10-second `LISTENING` timer or `no-speech` error dispatches `event="silence"` to `/api/voice/turn`, prompting *"Sorry, I didn't catch anything—are you still there?"* without altering booking state. |
| **Repeat Requests (`"Sorry, can you repeat that"`)** | **LiveKit:** tracks `last_assistant_reply` and exposes `repeat_last_reply` tool. **Browser Voice:** matches repeat phrases (`repeat`, `say that again`, `pardon`, `didn't catch`) and replays `state.last_reply` verbatim. |

---

## Project Structure

```text
voice-clinic-agent/
├── agent/
│   ├── main.py             # LiveKit Agents worker (Groq STT + Groq LLM + Cartesia/Deepgram TTS + barge-in/silence)
│   ├── metrics_tracker.py  # Per-turn latency aggregator (EOU, STT, LLM TTFT, Tool ms, TTS TTFB)
│   └── tools.py            # Persistent HTTPX client calling FastAPI tools with timing telemetry
├── backend/
│   ├── db.py               # SQLite schema & seeded clinic doctors/slots/bookings
│   ├── main.py             # FastAPI clinic REST API, SSE stream, /api/voice/turn, LiveKit token endpoint
│   ├── models.py           # Pydantic request/response schemas
│   └── voice_engine.py     # Stateful keyless voice turn processor with read-back verification & edge cases
├── docs/
│   └── demo.gif            # Recorded live call + verification + schedule update demo
├── frontend/
│   ├── index.html          # Real-time voice console (LiveKit + Keyless Web Speech API + fallback simulation)
│   ├── styles.css
│   └── app.js
├── scripts/
│   ├── benchmark_tools.py  # Cross-platform live loopback TCP + FastAPI + SQLite latency benchmark
│   └── run.py              # Cross-platform launcher (macOS, Linux, Windows)
├── tests/
│   ├── test_agent_tools.py
│   ├── test_backend.py
│   └── test_voice_edge_cases.py
├── pyproject.toml
└── requirements.txt
```

---

## Cross-Platform Setup (macOS, Linux, Windows)

### 1. Create Virtual Environment & Install Dependencies

**macOS / Linux (`bash` / `zsh`):**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
```

Or initialize `.env` cross-platform with:
```bash
python scripts/run.py init-env
```

### 2. Run the Application

**Terminal 1 — Start the FastAPI Clinic Service & Voice UI (works immediately with zero keys for Keyless Browser Voice):**
```bash
python scripts/run.py serve
```
Open [http://127.0.0.1:8000](http://127.0.0.1:8000) (Swagger API docs at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)).

**Terminal 2 — Start the LiveKit + Groq Voice Agent Worker (when `.env` has LiveKit, Groq, and Cartesia/Deepgram keys):**
```bash
python scripts/run.py agent
```

### 3. Run Tests & Latency Benchmark

```bash
python scripts/run.py test
python scripts/run.py bench
```

---

## Measured Latency Numbers

### 1. Measured FastAPI + SQLite Tool Calling Latency (Loopback TCP)

Measured locally via `python scripts/benchmark_tools.py` using `ClinicAPIClient` (`httpx.AsyncClient` with HTTP keep-alive) against a live `uvicorn` + SQLite server (`20` reads for `check_available_slots`; `15` full book → lookup → reschedule → cancel cycles):

| Tool / Endpoint | Mean | p50 (Median) | p95 | Min | Max |
|---|---:|---:|---:|---:|---:|
| `check_available_slots` (`GET /api/slots`) | 2.18 ms | 2.10 ms | 2.60 ms | 1.90 ms | 2.90 ms |
| `book_appointment` (`POST /api/appointments`) | 6.14 ms | 6.10 ms | 7.40 ms | 5.40 ms | 7.40 ms |
| `lookup_appointment` (`GET /api/appointments/lookup`) | 2.41 ms | 2.30 ms | 2.90 ms | 2.10 ms | 3.00 ms |
| `reschedule_appointment` (`PATCH /api/appointments/{id}/reschedule`) | 6.01 ms | 6.00 ms | 6.60 ms | 5.30 ms | 6.60 ms |
| `cancel_appointment` (`DELETE /api/appointments/{id}`) | 5.15 ms | 5.00 ms | 6.00 ms | 4.30 ms | 6.70 ms |

### 2. End-to-End Voice Pipeline Latency Breakdown

Per-turn response time is tracked in [agent/metrics_tracker.py](file:///C:/Users/nkuma/.gemini/antigravity/scratch/voice-clinic-agent/agent/metrics_tracker.py) and streamed to the UI as both `perceived_total_ms` (wall-clock from `thinking` to `speaking`) and `pipeline_sum_ms` (`eou_ms + llm_ttft_ms + tool_total_ms + tts_ttfb_ms`):

| Pipeline Stage | Component | Measured / Expected Behavior |
|---|---|---|
| Turn Detection (VAD/EOU) | Silero VAD (`silero.VAD.load()`) | Local ONNX inference (`EOUMetrics.end_of_utterance_delay`) |
| Speech-to-Text (STT) | Groq `whisper-large-v3-turbo` (or Web `SpeechRecognition` in keyless mode) | Cloud inference (`STTMetrics.duration`, depends on network RTT + audio length) |
| LLM First Token (TTFT) | Groq `llama-3.3-70b-versatile` (or `/api/voice/turn` NLU in keyless mode) | Cloud inference (`LLMMetrics.ttft` + `tokens_per_second`) |
| Clinic Tool Call | FastAPI + SQLite (`ClinicAPIClient`) | **2.1–6.1 ms median** locally (see table above) |
| Voice Synthesis (TTFB) | Cartesia `sonic-2` / Deepgram `aura-2` (or `speechSynthesis` in keyless mode) | Streaming first audio byte (`TTSMetrics.ttfb`) |

*(Note: LiveKit/Groq/Cartesia cloud latencies depend on live API keys and regional network RTT; Keyless Browser Voice measures real local FastAPI round-trip + browser `speechSynthesis` onset, while Keyless Fallback Simulation replays tool calls against the live FastAPI + SQLite backend.)*

---

## Design Decisions & Tradeoffs

1. **Modular Pipeline (LiveKit + Groq + Cartesia/Deepgram) vs. Monolithic Speech-to-Speech**
   - *Choice:* Separate STT (`whisper-large-v3-turbo`), LLM (`llama-3.3-70b-versatile`), and streaming TTS (`sonic-2` / `aura-2`) orchestrated by LiveKit `AgentSession`.
   - *Tradeoff:* Adds minor stage-handoff overhead compared to single-model audio-in/audio-out APIs, in exchange for strict `@function_tool` verification guards, per-stage latency attribution (`EOU`, `LLM TTFT`, `Tool`, `TTS TTFB`), independent model swapping, and lower per-minute cost.

2. **Tool-Layer `confirmed=False` Guard vs. Prompt-Only Confirmation**
   - *Choice:* Both the LiveKit agent's `@function_tool` signatures and `/api/voice/turn` require explicit confirmation before writing to SQLite.
   - *Tradeoff:* Adds one extra conversational turn (`"Shall I confirm?"`), but prevents STT mishearings (names, digits, dates) from creating invalid appointments.

3. **Keyless Web Speech API Mode + Stateful Backend NLU**
   - *Choice:* Pair browser `SpeechRecognition` + `speechSynthesis` with `POST /api/voice/turn` (`backend/voice_engine.py`) alongside the primary LiveKit + Groq pipeline.
   - *Tradeoff:* Browser Web Speech recognition varies by browser/OS (best on Chromium; unsupported on some browsers) and lacks Silero/Groq acoustic accuracy, but lets any reviewer speak to the agent and test real SQLite bookings with zero API keys.

4. **Persistent `httpx.AsyncClient` + SQLite `BEGIN IMMEDIATE`**
   - *Choice:* Session-scoped HTTP keep-alive pool (`agent/tools.py`) calling SQLite with `BEGIN IMMEDIATE` transactions.
   - *Tradeoff:* Single-host write concurrency only, in exchange for zero external DB setup and **2–6 ms** tool latency.

---

## Known Limitations & Next Steps

### Known Limitations
- **Browser Support for Keyless Voice:** `SpeechRecognition` is supported in Chromium-based browsers (Chrome, Edge) and requires HTTPS or `localhost`; other browsers fall back to the built-in turn text input or LiveKit WebRTC.
- **Echo During Browser `speechSynthesis`:** Without headphones, speakers can trigger `SpeechRecognition` barge-in prematurely in Web Speech mode (LiveKit WebRTC handles acoustic echo cancellation natively).
- **Single-Instance State & SSE:** SQLite locking and in-memory `_sse_subscribers` (`asyncio.Queue`) assume `--workers 1`.
- **No Patient Auth or HIPAA/PII Redaction:** Names, phone numbers, and visit reasons are stored unencrypted in SQLite for demo visibility.

### Next Steps
- **Database & Pub/Sub Scaling:** Migrate `backend/db.py` to PostgreSQL (`SELECT ... FOR UPDATE`) + Redis pub/sub for multi-worker SSE fan-out.
- **Telephony Integration:** Attach a LiveKit SIP trunk so callers can dial a real clinic phone number into `ClinicReceptionistAgent`.
- **Acoustic & Model Tuning:** Benchmark `llama-3.1-8b-instant` vs `llama-3.3-70b-versatile` on live Groq calls and tune VAD/endpointing delays per deployment environment.
- **Auth & Compliance:** Add caller verification (DOB/OTP), PII masking in telemetry logs, and OpenTelemetry trace export.
