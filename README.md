# PulseCare Voice Desk — Real-Time Voice Agent (LiveKit + Groq + FastAPI)

An ultra-low-latency AI voice receptionist for **PulseCare Medical & Dental Clinic**, built with:
- **LiveKit Agents (`~1.0`)**: Real-time WebRTC audio transport, Silero VAD turn-taking, and telemetry data channel
- **Groq (`whisper-large-v3-turbo` + `llama-3.3-70b-versatile`)**: Sub-200ms Whisper STT and high-throughput LLM tool-calling inference
- **Cartesia (`sonic-2`) or Deepgram (`aura-2`)**: Streaming low-latency Text-to-Speech
- **FastAPI + SQLite**: Atomic appointment slot checking, booking, lookup, rescheduling, and cancellation with Server-Sent Events (`/api/events`)
- **Live Latency Dashboard**: Real-time transcript, per-turn response time badges, latency waterfall (`EOU/VAD + Groq LLM TTFT + FastAPI Tool + TTS TTFB`), and live clinic schedule board

## Project Structure

```text
voice-clinic-agent/
├── backend/
│   ├── db.py           # SQLite schema & seeded clinic doctors/slots/bookings
│   ├── models.py       # Pydantic request/response schemas
│   └── main.py         # FastAPI clinic REST API, SSE stream, LiveKit token endpoint, static UI
├── agent/
│   ├── metrics_tracker.py  # Per-turn latency aggregator (STT/EOU, LLM TTFT, Tool ms, TTS TTFB)
│   ├── tools.py        # Persistent HTTPX client calling FastAPI tools with timing telemetry
│   └── main.py         # LiveKit Agents worker (Groq STT + Groq LLM + Cartesia/Deepgram TTS)
├── frontend/
│   ├── index.html      # Live voice console + latency waterfall + live clinic schedule
│   ├── styles.css
│   └── app.js          # LiveKit WebRTC client + DataChannel telemetry + Simulated Call walkthrough
└── tests/
    ├── test_backend.py
    └── test_agent_tools.py
```

## Setup

1. Create and activate the virtual environment, then install dependencies:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Configure API keys:
   ```powershell
   copy .env.example .env
   ```
   Fill in `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `GROQ_API_KEY`, and `CARTESIA_API_KEY` (or set `TTS_PROVIDER=deepgram` and `DEEPGRAM_API_KEY`).

## Running the System

1. **Start the FastAPI Clinic Service & Web UI** (Terminal 1):
   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
   ```
   Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

2. **Start the LiveKit + Groq Voice Agent Worker** (Terminal 2):
   ```powershell
   .\.venv\Scripts\python.exe -m agent.main dev
   ```

3. **Test Live**:
   - With LiveKit/Groq/TTS keys configured: click **Connect Live Voice**, allow microphone access, and ask Maya to check dental slots tomorrow, book an appointment, or look up `BK-1042` (`555-0192`). Watch the per-turn response latency and clinic schedule update live.
   - Without external keys yet: click **Simulate Live Call (Real FastAPI Tools)** in the UI to run a multi-turn booking walkthrough that hits the real FastAPI + SQLite endpoints and updates the schedule over SSE.

## Running Automated Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```
