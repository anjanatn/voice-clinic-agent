from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .models import (
    Appointment,
    BookAppointmentRequest,
    Doctor,
    LiveKitTokenRequest,
    LiveKitTokenResponse,
    RescheduleAppointmentRequest,
    TimeSlot,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / ".env.local", override=True)

FRONTEND_DIR = ROOT_DIR / "frontend"

_sse_subscribers: set[asyncio.Queue[str]] = set()


async def _broadcast_event(event_type: str, payload: dict) -> None:
    if not _sse_subscribers:
        return
    message = f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
    for queue in list(_sse_subscribers):
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    yield


app = FastAPI(
    title="PulseCare Clinic Scheduling API",
    description="FastAPI tool-calling backend and LiveKit token service for real-time Groq voice agent.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _is_placeholder(val: str | None) -> bool:
    if not val:
        return True
    lowered = val.strip().lower()
    return (
        not lowered
        or "your_" in lowered
        or "your-" in lowered
        or lowered == "gsk_your_groq_api_key"
    )


@app.get("/api/health")
def health() -> dict:
    slots = db.query_slots(include_booked=True)
    open_slots = sum(1 for s in slots if not s.is_booked)
    appointments = db.list_appointments()

    livekit_ready = not (
        _is_placeholder(os.getenv("LIVEKIT_URL"))
        or _is_placeholder(os.getenv("LIVEKIT_API_KEY"))
        or _is_placeholder(os.getenv("LIVEKIT_API_SECRET"))
    )
    groq_ready = not _is_placeholder(os.getenv("GROQ_API_KEY"))
    tts_provider = os.getenv("TTS_PROVIDER", "cartesia").lower()
    tts_ready = not _is_placeholder(
        os.getenv("DEEPGRAM_API_KEY") if tts_provider == "deepgram" else os.getenv("CARTESIA_API_KEY")
    )

    return {
        "status": "ok",
        "clinic": "PulseCare Medical & Dental Clinic",
        "total_slots": len(slots),
        "available_slots": open_slots,
        "booked_appointments": len(appointments),
        "livekit_configured": livekit_ready,
        "groq_configured": groq_ready,
        "tts_provider": tts_provider,
        "tts_configured": tts_ready,
        "stt_model": os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
        "llm_model": os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile"),
    }


@app.get("/api/doctors", response_model=list[Doctor])
def get_doctors() -> list[Doctor]:
    return db.list_doctors()


@app.get("/api/slots", response_model=list[TimeSlot])
def get_slots(
    specialty: str | None = Query(default=None),
    doctor_name: str | None = Query(default=None),
    date: str | None = Query(default=None, description="YYYY-MM-DD, 'today', or 'tomorrow'"),
    include_booked: bool = Query(default=False),
) -> list[TimeSlot]:
    return db.query_slots(
        specialty=specialty,
        doctor_name=doctor_name,
        date=date,
        include_booked=include_booked,
    )


@app.get("/api/appointments", response_model=list[Appointment])
def get_appointments() -> list[Appointment]:
    return db.list_appointments()


@app.get("/api/appointments/lookup", response_model=list[Appointment])
def lookup_appointment(
    booking_id: str | None = Query(default=None),
    phone: str | None = Query(default=None),
    patient_name: str | None = Query(default=None),
) -> list[Appointment]:
    if not any([booking_id, phone, patient_name]):
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of booking_id, phone, or patient_name.",
        )
    return db.lookup_appointments(
        booking_id=booking_id,
        phone=phone,
        patient_name=patient_name,
    )


@app.post("/api/appointments", response_model=Appointment, status_code=201)
async def create_appointment(payload: BookAppointmentRequest) -> Appointment:
    try:
        appointment = db.book_slot(
            slot_id=payload.slot_id,
            patient_name=payload.patient_name,
            phone=payload.phone,
            reason=payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _broadcast_event("schedule_updated", {"action": "booked", "booking_id": appointment.booking_id})
    return appointment


@app.patch("/api/appointments/{booking_id}/reschedule", response_model=Appointment)
async def reschedule_appointment(
    booking_id: str,
    payload: RescheduleAppointmentRequest,
) -> Appointment:
    try:
        appointment = db.reschedule_appointment(
            booking_id=booking_id,
            new_slot_id=payload.new_slot_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await _broadcast_event("schedule_updated", {"action": "rescheduled", "booking_id": appointment.booking_id})
    return appointment


@app.delete("/api/appointments/{booking_id}", response_model=Appointment)
async def delete_appointment(booking_id: str) -> Appointment:
    try:
        cancelled = db.cancel_appointment(booking_id=booking_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await _broadcast_event("schedule_updated", {"action": "cancelled", "booking_id": cancelled.booking_id})
    return cancelled


@app.post("/api/reset")
async def reset_demo_data() -> dict:
    db.init_db(force_reset=True)
    await _broadcast_event("schedule_updated", {"action": "reset"})
    return {"status": "reset", "appointments": len(db.list_appointments())}


@app.get("/api/events")
async def schedule_events() -> StreamingResponse:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
    _sse_subscribers.add(queue)

    async def event_stream() -> AsyncIterator[str]:
        try:
            yield "event: connected\ndata: {}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_subscribers.discard(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/livekit-token", response_model=LiveKitTokenResponse)
def create_livekit_token(payload: LiveKitTokenRequest) -> LiveKitTokenResponse:
    lk_url = os.getenv("LIVEKIT_URL", "")
    lk_key = os.getenv("LIVEKIT_API_KEY", "")
    lk_secret = os.getenv("LIVEKIT_API_SECRET", "")
    tts_provider = os.getenv("TTS_PROVIDER", "cartesia").lower()
    stt_model = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
    llm_model = os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")

    if _is_placeholder(lk_url) or _is_placeholder(lk_key) or _is_placeholder(lk_secret):
        return LiveKitTokenResponse(
            token="",
            url=lk_url,
            room_name=payload.room_name,
            participant_name=payload.participant_name,
            configured=False,
            tts_provider=tts_provider,
            stt_model=stt_model,
            llm_model=llm_model,
        )

    from livekit import api as lk_api

    token = (
        lk_api.AccessToken(lk_key, lk_secret)
        .with_identity(f"patient-{payload.participant_name.lower().replace(' ', '-')}")
        .with_name(payload.participant_name)
        .with_grants(
            lk_api.VideoGrants(
                room_join=True,
                room=payload.room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )

    return LiveKitTokenResponse(
        token=token,
        url=lk_url,
        room_name=payload.room_name,
        participant_name=payload.participant_name,
        configured=True,
        tts_provider=tts_provider,
        stt_model=stt_model,
        llm_model=llm_model,
    )


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def serve_index() -> FileResponse:
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not built yet.")
    return FileResponse(str(index_path))
