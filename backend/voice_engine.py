from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import db

WORD_DIGITS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}

SPECIALTY_KEYWORDS = {
    "dentist": "Dentistry",
    "dental": "Dentistry",
    "dentistry": "Dentistry",
    "tooth": "Dentistry",
    "teeth": "Dentistry",
    "cleaning": "Dentistry",
    "rostova": "Dentistry",
    "skin": "Dermatology",
    "derm": "Dermatology",
    "dermatology": "Dermatology",
    "dermatologist": "Dermatology",
    "rash": "Dermatology",
    "chen": "Dermatology",
    "child": "Pediatrics",
    "kid": "Pediatrics",
    "pediatric": "Pediatrics",
    "pediatrics": "Pediatrics",
    "pediatrician": "Pediatrics",
    "nair": "Pediatrics",
    "general": "General Medicine",
    "medicine": "General Medicine",
    "physical": "General Medicine",
    "checkup": "General Medicine",
    "patel": "General Medicine",
}


@dataclass
class VoiceSessionState:
    session_id: str
    default_patient_name: str = ""
    pending_specialty: str | None = None
    pending_date: str | None = None
    pending_slot: dict[str, Any] | None = None
    pending_name: str | None = None
    pending_phone: str | None = None
    pending_reason: str | None = None
    awaiting_booking_confirmation: bool = False
    awaiting_cancel_booking_id: str | None = None
    offered_slots: list[dict[str, Any]] = field(default_factory=list)
    last_reply: str = (
        "Hi, I'm Maya from PulseCare Medical and Dental Clinic. "
        "How can I help with appointments today?"
    )
    silence_count: int = 0


_SESSIONS: dict[str, VoiceSessionState] = {}


def get_or_create_session(session_id: str, patient_name: str = "") -> VoiceSessionState:
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = VoiceSessionState(
            session_id=session_id,
            default_patient_name=patient_name.strip(),
        )
    elif patient_name.strip():
        _SESSIONS[session_id].default_patient_name = patient_name.strip()
    return _SESSIONS[session_id]


def reset_sessions() -> None:
    _SESSIONS.clear()


def _normalize_spoken_digits(text: str) -> str:
    tokens = re.split(r"(\s+)", text.lower())
    out = [WORD_DIGITS.get(tok, tok) for tok in tokens]
    return "".join(out)


def _extract_phone(text: str) -> str | None:
    normalized = _normalize_spoken_digits(text)
    digits_groups = re.findall(r"(?:\d[\s\-\.]*){7,11}", normalized)
    for grp in digits_groups:
        digits = "".join(ch for ch in grp if ch.isdigit())
        if 7 <= len(digits) <= 11:
            if len(digits) == 7:
                return f"{digits[:3]}-{digits[3:]}"
            if len(digits) == 10:
                return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
            return digits
    return None


def _extract_booking_id(text: str) -> str | None:
    match = re.search(r"\bBK[\s\-]?(\d{3,6})\b", text, flags=re.IGNORECASE)
    if match:
        return f"BK-{match.group(1)}"
    return None


def _extract_slot_id(text: str) -> str | None:
    match = re.search(r"\bSLOT[\s\-]?(\d{1,4})\b", text, flags=re.IGNORECASE)
    if match:
        return f"SLOT-{int(match.group(1)):03d}"
    return None


def _extract_specialty(text: str) -> str | None:
    lowered = text.lower()
    for kw, specialty in SPECIALTY_KEYWORDS.items():
        if re.search(rf"\b{re.escape(kw)}\b", lowered):
            return specialty
    return None


def _extract_date(text: str) -> str | None:
    lowered = text.lower()
    if "today" in lowered:
        return "today"
    if "tomorrow" in lowered:
        return "tomorrow"
    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if iso_match:
        return iso_match.group(1)
    weekdays = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    today = datetime.now().date()
    for idx, day_name in enumerate(weekdays):
        if day_name in lowered:
            delta = (idx - today.weekday()) % 7
            if delta == 0:
                delta = 7
            return (today + timedelta(days=delta)).isoformat()
    return None


def _extract_time_preference(text: str) -> str | None:
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b", text.lower())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    ampm = (match.group(3) or "").replace(".", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    elif not ampm and 1 <= hour <= 6:
        hour += 12
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return None


def _extract_name(text: str) -> str | None:
    patterns = [
        r"(?:my name is|name is|this is|i am|i'm|under|for)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
        r"(?:my name is|name is|this is|i am|i'm|under)\s+([a-z]+(?:\s+[a-z]+){1,2})",
    ]
    stopwords = {"a", "an", "the", "dental", "dentistry", "dermatology", "pediatrics", "tomorrow", "today", "checkup", "cleaning"}
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE if pat == patterns[1] else 0)
        if m:
            candidate = " ".join(w.capitalize() for w in m.group(1).strip().split())
            words = [w.lower() for w in candidate.split()]
            if words and not any(w in stopwords for w in words):
                return candidate
    return None


def _is_affirmative(text: str) -> bool:
    lowered = text.lower().strip()
    return bool(
        re.search(
            r"\b(yes|yeah|yep|yup|confirm|confirmed|sure|go ahead|book it|do it|correct|sounds good|please do|ok|okay)\b",
            lowered,
        )
    )


def _is_negative(text: str) -> bool:
    lowered = text.lower().strip()
    return bool(
        re.search(
            r"\b(no|nope|nah|wait|stop|cancel|wrong|change|don't|do not|actually)\b",
            lowered,
        )
    )


def _is_repeat_request(text: str) -> bool:
    lowered = text.lower().strip()
    return bool(
        re.search(
            r"\b(repeat|say that again|come again|pardon|didn't hear|did not hear|didn't catch|did not catch|what did you say)\b",
            lowered,
        )
    )


def _format_spoken_phone(phone: str) -> str:
    digits = [ch for ch in phone if ch.isdigit()]
    if len(digits) == 7:
        return f"{' '.join(digits[:3])}, {' '.join(digits[3:])}"
    return " ".join(digits) if digits else phone


def _build_readback(state: VoiceSessionState) -> str:
    slot = state.pending_slot or {}
    name = state.pending_name or "the patient"
    phone = state.pending_phone or "unknown"
    reason = state.pending_reason or "General consultation"
    return (
        f"Let me verify before booking: {slot.get('specialty', 'Clinic')} with "
        f"{slot.get('doctor_name', 'our doctor')} on {slot.get('date')} at {slot.get('start_time')}, "
        f"for {name}, phone {_format_spoken_phone(phone)}, reason {reason}. Shall I confirm?"
    )


def process_voice_turn(
    session_id: str,
    transcript: str = "",
    event: str = "speech",
    patient_name: str = "",
) -> dict[str, Any]:
    """Stateful, keyless conversational voice turn processor backed by SQLite clinic tools."""
    t_start = time.perf_counter()
    state = get_or_create_session(session_id, patient_name=patient_name)
    tool_events: list[dict[str, Any]] = []
    schedule_changed = False

    def record_tool(name: str, args: dict[str, Any], t0: float, success: bool, result: Any) -> None:
        tool_events.append(
            {
                "type": "tool_call",
                "name": name,
                "args": args,
                "duration_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                "success": success,
                "result": result,
            }
        )

    clean_text = transcript.strip()

    # 1. Handle silence / empty STT gracefully
    if event == "silence" or not clean_text:
        state.silence_count += 1
        if state.awaiting_booking_confirmation and state.pending_slot:
            reply = f"I didn't hear anything. {_build_readback(state)}"
        elif state.silence_count == 1:
            reply = (
                "Sorry, I didn't catch anything—are you still there? "
                "You can ask to check dental slots tomorrow or look up an existing booking."
            )
        else:
            reply = (
                "I'm still here whenever you're ready. Just say a specialty like dentistry "
                "or dermatology, or say a booking ID like BK-1042."
            )
        return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

    state.silence_count = 0

    # 2. Handle "sorry, can you repeat that"
    if _is_repeat_request(clean_text):
        reply = f"Of course. {state.last_reply}"
        return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

    # Extract any entities mentioned in this utterance
    found_specialty = _extract_specialty(clean_text)
    found_date = _extract_date(clean_text)
    found_slot_id = _extract_slot_id(clean_text)
    found_booking_id = _extract_booking_id(clean_text)
    found_phone = _extract_phone(clean_text)
    found_name = _extract_name(clean_text)
    found_time = _extract_time_preference(clean_text)

    if found_specialty:
        state.pending_specialty = found_specialty
    if found_date:
        state.pending_date = found_date
    if found_name:
        state.pending_name = found_name
    if found_phone:
        state.pending_phone = found_phone

    lowered = clean_text.lower()

    # 3. Handle pending cancellation confirmation
    if state.awaiting_cancel_booking_id:
        bid = state.awaiting_cancel_booking_id
        if _is_affirmative(clean_text):
            t0 = time.perf_counter()
            try:
                cancelled = db.cancel_appointment(bid)
                record_tool("cancel_appointment", {"booking_id": bid}, t0, True, cancelled.model_dump())
                state.awaiting_cancel_booking_id = None
                schedule_changed = True
                reply = (
                    f"Done. Booking {cancelled.booking_id} on {cancelled.date} at "
                    f"{cancelled.start_time} has been cancelled and the slot is open again."
                )
            except LookupError as exc:
                record_tool("cancel_appointment", {"booking_id": bid}, t0, False, {"error": str(exc)})
                state.awaiting_cancel_booking_id = None
                reply = f"I couldn't find booking {bid}. Please check the booking ID."
            return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

        if _is_negative(clean_text):
            state.awaiting_cancel_booking_id = None
            reply = f"Okay, I kept booking {bid} active. Anything else I can help with?"
            return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

    # 4. Handle pending booking verification ("Shall I confirm?")
    if state.awaiting_booking_confirmation and state.pending_slot:
        if _is_negative(clean_text) and not _is_affirmative(clean_text):
            # Allow inline correction (e.g. "no, my name is Alex" or "no, change the time")
            if found_name or found_phone or found_time or found_slot_id:
                if found_slot_id:
                    matched = [s for s in state.offered_slots if s["id"] == found_slot_id]
                    if matched:
                        state.pending_slot = matched[0]
                elif found_time and state.offered_slots:
                    matched = [s for s in state.offered_slots if s["start_time"] == found_time]
                    if matched:
                        state.pending_slot = matched[0]
                reply = f"Got it, updated that. {_build_readback(state)}"
                return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

            state.awaiting_booking_confirmation = False
            reply = (
                "No problem, I haven't booked anything yet. "
                "Tell me which detail—name, phone number, or time—you'd like to change."
            )
            return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

        if _is_affirmative(clean_text) and not (found_name or found_phone):
            slot = state.pending_slot
            t0 = time.perf_counter()
            try:
                booked = db.book_slot(
                    slot_id=slot["id"],
                    patient_name=state.pending_name or state.default_patient_name or "Walk-in Patient",
                    phone=state.pending_phone or "555-0100",
                    reason=state.pending_reason or "Clinic consultation",
                )
                record_tool(
                    "book_appointment",
                    {
                        "slot_id": slot["id"],
                        "patient_name": booked.patient_name,
                        "phone": booked.phone,
                        "reason": booked.reason,
                        "confirmed": True,
                    },
                    t0,
                    True,
                    booked.model_dump(),
                )
                state.awaiting_booking_confirmation = False
                state.pending_slot = None
                state.offered_slots = []
                schedule_changed = True
                reply = (
                    f"Confirmed! Your booking ID is {booked.booking_id} with {booked.doctor_name} "
                    f"on {booked.date} at {booked.start_time}. Anything else I can help with?"
                )
            except (LookupError, ValueError) as exc:
                record_tool("book_appointment", {"slot_id": slot["id"]}, t0, False, {"error": str(exc)})
                state.awaiting_booking_confirmation = False
                state.pending_slot = None
                reply = f"That slot was just taken. Let's pick another open time."
            return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

        # User gave updated name/phone while awaiting confirmation -> re-read back!
        if found_name or found_phone:
            reply = f"Updated. {_build_readback(state)}"
            return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

        reply = f"Just to be sure—{_build_readback(state)}"
        return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

    # 5. Cancellation intent
    if "cancel" in lowered and (found_booking_id or found_phone or found_name):
        t0 = time.perf_counter()
        matches = db.lookup_appointments(
            booking_id=found_booking_id,
            phone=found_phone,
            patient_name=found_name,
        )
        record_tool(
            "lookup_appointment",
            {"booking_id": found_booking_id, "phone": found_phone, "patient_name": found_name},
            t0,
            True,
            {"count": len(matches), "appointments": [m.model_dump() for m in matches]},
        )
        if not matches:
            reply = "I couldn't find an appointment matching that. Could you repeat the booking ID or phone number?"
        else:
            target = matches[0]
            state.awaiting_cancel_booking_id = target.booking_id
            reply = (
                f"I found {target.booking_id} for {target.patient_name} with {target.doctor_name} "
                f"on {target.date} at {target.start_time}. Shall I confirm cancelling it?"
            )
        return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

    # 6. Lookup existing appointment
    if any(k in lowered for k in ("look up", "lookup", "confirm booking", "my booking", "my appointment", "find booking", "check booking")) or found_booking_id:
        t0 = time.perf_counter()
        matches = db.lookup_appointments(
            booking_id=found_booking_id,
            phone=found_phone,
            patient_name=found_name,
        )
        record_tool(
            "lookup_appointment",
            {"booking_id": found_booking_id, "phone": found_phone, "patient_name": found_name},
            t0,
            True,
            {"count": len(matches), "appointments": [m.model_dump() for m in matches]},
        )
        if not matches:
            reply = "I didn't find a matching appointment. Please share your booking ID like BK-1042 or your phone number."
        else:
            a = matches[0]
            reply = (
                f"Found booking {a.booking_id} for {a.patient_name} with {a.doctor_name} "
                f"in {a.specialty} on {a.date} at {a.start_time}, phone {_format_spoken_phone(a.phone)}."
            )
        return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

    # 7. Select a slot from previously offered slots
    if state.offered_slots and not state.pending_slot:
        chosen = None
        if found_slot_id:
            chosen = next((s for s in state.offered_slots if s["id"] == found_slot_id), None)
        elif found_time:
            chosen = next((s for s in state.offered_slots if s["start_time"] == found_time), None)
        elif "first" in lowered or _is_affirmative(clean_text):
            chosen = state.offered_slots[0]
        elif "second" in lowered and len(state.offered_slots) > 1:
            chosen = state.offered_slots[1]

        if chosen is not None:
            state.pending_slot = chosen

    # 8. Check slots if user asks for availability, specialty, or date
    wants_slots = any(
        k in lowered
        for k in ("slot", "available", "availability", "opening", "appointment", "book", "schedule", "dent", "derm", "pediatric", "general", "doctor")
    ) or found_specialty or found_date

    if wants_slots and state.pending_slot is None:
        t0 = time.perf_counter()
        slots = db.query_slots(
            specialty=state.pending_specialty,
            date=state.pending_date,
        )
        slot_dicts = [s.model_dump() for s in slots[:5]]
        record_tool(
            "check_available_slots",
            {"specialty": state.pending_specialty, "date": state.pending_date},
            t0,
            True,
            {"count": len(slots), "slots": slot_dicts},
        )
        state.offered_slots = slot_dicts
        if not slots:
            reply = (
                f"I don't see open slots for {state.pending_specialty or 'that filter'} "
                f"on {state.pending_date or 'that day'}. Would you like me to check another day or specialty?"
            )
            return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

        if found_time:
            time_match = next((s for s in slot_dicts if s["start_time"] == found_time), None)
            if time_match:
                state.pending_slot = time_match

        if state.pending_slot is None:
            first = slot_dicts[0]
            second = slot_dicts[1] if len(slot_dicts) > 1 else None
            date_label = first["date"]
            if second:
                reply = (
                    f"For {first['specialty']} on {date_label}, {first['doctor_name']} has "
                    f"{first['start_time']} in {first['id']} and {second['start_time']} in {second['id']}. "
                    "Which time works best, and what is your full name and phone number?"
                )
            else:
                reply = (
                    f"I found {first['specialty']} with {first['doctor_name']} on {date_label} "
                    f"at {first['start_time']} in {first['id']}. Would you like that slot?"
                )
            return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

    # 9. If a slot is selected, gather name + phone, then read back & ask "Shall I confirm?"
    if state.pending_slot is not None:
        if any(w in lowered for w in ("cleaning", "checkup", "physical", "rash", "pain", "follow up", "consultation")):
            state.pending_reason = clean_text if len(clean_text) < 80 else "Clinic visit"
        elif not state.pending_reason:
            state.pending_reason = f"{state.pending_slot['specialty']} visit"

        missing = []
        if not state.pending_name:
            missing.append("your full name")
        if not state.pending_phone:
            missing.append("your phone number")

        if missing:
            slot = state.pending_slot
            ack_parts = []
            if state.pending_name:
                ack_parts.append(f"name {state.pending_name}")
            if state.pending_phone:
                ack_parts.append(f"phone {_format_spoken_phone(state.pending_phone)}")
            ack = f"I have {', '.join(ack_parts)}. " if ack_parts else ""
            reply = (
                f"{ack}I'm holding {slot['doctor_name']} on {slot['date']} at {slot['start_time']} "
                f"({slot['id']}). Please tell me {' and '.join(missing)} so I can verify before booking."
            )
            return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

        # Both name & phone present -> ALWAYS read back and ask "Shall I confirm?"
        state.awaiting_booking_confirmation = True
        reply = _build_readback(state)
        return _finalize_response(state, reply, tool_events, t_start, schedule_changed)

    # Fallback helpful greeting
    reply = (
        "I can check open appointments for General Medicine, Dentistry, Dermatology, or Pediatrics, "
        "book a slot, or look up a booking ID. What would you like to do?"
    )
    return _finalize_response(state, reply, tool_events, t_start, schedule_changed)


def _finalize_response(
    state: VoiceSessionState,
    reply: str,
    tool_events: list[dict[str, Any]],
    t_start: float,
    schedule_changed: bool,
) -> dict[str, Any]:
    state.last_reply = reply
    total_ms = round((time.perf_counter() - t_start) * 1000.0, 1)
    tool_total_ms = round(sum(ev["duration_ms"] for ev in tool_events), 1)
    return {
        "reply": reply,
        "tool_events": tool_events,
        "tool_total_ms": tool_total_ms,
        "server_ms": total_ms,
        "awaiting_confirmation": state.awaiting_booking_confirmation or bool(state.awaiting_cancel_booking_id),
        "schedule_changed": schedule_changed,
    }
