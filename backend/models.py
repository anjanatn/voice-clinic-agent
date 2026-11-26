from __future__ import annotations

from pydantic import BaseModel, Field


class Doctor(BaseModel):
    id: str
    name: str
    specialty: str
    room: str


class TimeSlot(BaseModel):
    id: str
    doctor_id: str
    doctor_name: str
    specialty: str
    date: str
    start_time: str
    end_time: str
    is_booked: bool


class Appointment(BaseModel):
    booking_id: str
    slot_id: str
    doctor_id: str
    doctor_name: str
    specialty: str
    date: str
    start_time: str
    end_time: str
    patient_name: str
    phone: str
    reason: str
    status: str
    created_at: str


class BookAppointmentRequest(BaseModel):
    slot_id: str = Field(..., description="Unique slot identifier, e.g. SLOT-001")
    patient_name: str = Field(..., min_length=1, max_length=120)
    phone: str = Field(..., min_length=3, max_length=40)
    reason: str = Field(default="General consultation", max_length=250)


class RescheduleAppointmentRequest(BaseModel):
    new_slot_id: str = Field(..., description="New target slot identifier")


class LiveKitTokenRequest(BaseModel):
    room_name: str = Field(default="pulsecare-clinic-room")
    participant_name: str = Field(default="Patient")


class LiveKitTokenResponse(BaseModel):
    token: str
    url: str
    room_name: str
    participant_name: str
    configured: bool
    tts_provider: str
    stt_model: str
    llm_model: str
