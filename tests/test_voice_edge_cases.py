from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.main import ClinicReceptionistAgent
from agent.tools import ClinicAPIClient
from backend import db, voice_engine
from backend.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "voice_edge.db"
    monkeypatch.setenv("CLINIC_DB_PATH", str(db_file))
    db.init_db(db_file, force_reset=True)
    voice_engine.reset_sessions()
    with TestClient(app) as test_client:
        yield test_client


def test_keyless_voice_verify_repeat_correction_and_silence(client: TestClient):
    sid = "test-session-1"

    # 1. Silence handling
    silence_resp = client.post(
        "/api/voice/turn",
        json={"session_id": sid, "transcript": "", "event": "silence"},
    )
    assert silence_resp.status_code == 200
    assert "still there" in silence_resp.json()["reply"].lower()
    assert len(client.get("/api/appointments").json()) == 2

    # 2. Check dental slots tomorrow
    t1 = client.post(
        "/api/voice/turn",
        json={"session_id": sid, "transcript": "Do you have dentistry slots tomorrow?"},
    ).json()
    assert t1["tool_events"][0]["name"] == "check_available_slots"
    assert t1["schedule_changed"] is False
    assert len(client.get("/api/appointments").json()) == 2

    # 3. Provide slot + name + phone -> MUST read back and ask "Shall I confirm?" WITHOUT writing DB
    t2 = client.post(
        "/api/voice/turn",
        json={
            "session_id": sid,
            "transcript": "Book the 9 AM slot. My name is Jordan Lee and phone is 555-0184.",
        },
    ).json()
    assert t2["awaiting_confirmation"] is True
    assert t2["schedule_changed"] is False
    assert "Jordan Lee" in t2["reply"]
    assert "Shall I confirm?" in t2["reply"]
    assert len(client.get("/api/appointments").json()) == 2

    # 4. Repeat request -> repeats verification read-back verbatim without booking
    t3 = client.post(
        "/api/voice/turn",
        json={"session_id": sid, "transcript": "Sorry, can you repeat that?"},
    ).json()
    assert "Jordan Lee" in t3["reply"]
    assert "Shall I confirm?" in t3["reply"]
    assert len(client.get("/api/appointments").json()) == 2

    # 5. Caller corrects misheard name -> re-reads back without booking yet
    t4 = client.post(
        "/api/voice/turn",
        json={"session_id": sid, "transcript": "No, my name is Jordan Ray."},
    ).json()
    assert t4["awaiting_confirmation"] is True
    assert "Jordan Ray" in t4["reply"]
    assert "Shall I confirm?" in t4["reply"]
    assert len(client.get("/api/appointments").json()) == 2

    # 6. Caller confirms -> NOW writes booking to SQLite
    t5 = client.post(
        "/api/voice/turn",
        json={"session_id": sid, "transcript": "Yes, confirm it."},
    ).json()
    assert t5["schedule_changed"] is True
    assert t5["awaiting_confirmation"] is False
    assert t5["tool_events"][0]["name"] == "book_appointment"
    appts = client.get("/api/appointments").json()
    assert len(appts) == 3
    assert any(a["patient_name"] == "Jordan Ray" for a in appts)


@pytest.mark.asyncio
async def test_livekit_agent_tool_requires_confirmation(tmp_path, monkeypatch):
    db_file = tmp_path / "lk_confirm.db"
    monkeypatch.setenv("CLINIC_DB_PATH", str(db_file))
    db.init_db(db_file, force_reset=True)

    transport = httpx.ASGITransport(app=app)
    api_client = ClinicAPIClient(base_url="http://testserver", transport=transport)
    agent = ClinicReceptionistAgent(api_client=api_client)
    try:
        slots = await api_client.check_available_slots(specialty="Dentistry")
        slot_id = slots["slots"][0]["id"]

        # Unconfirmed call must NOT write to DB
        unconfirmed_raw = await agent.book_appointment(
            None,  # type: ignore[arg-type]
            slot_id=slot_id,
            patient_name="Taylor Brooks",
            phone="555-0166",
            reason="Dental exam",
            confirmed=False,
        )
        unconfirmed = json.loads(unconfirmed_raw)
        assert unconfirmed["status"] == "awaiting_confirmation"
        assert unconfirmed["database_written"] is False
        assert len(db.list_appointments(db_file)) == 2

        # Confirmed call writes to DB
        confirmed_raw = await agent.book_appointment(
            None,  # type: ignore[arg-type]
            slot_id=slot_id,
            patient_name="Taylor Brooks",
            phone="555-0166",
            reason="Dental exam",
            confirmed=True,
        )
        confirmed = json.loads(confirmed_raw)
        assert confirmed["booking_id"].startswith("BK-")
        assert len(db.list_appointments(db_file)) == 3
    finally:
        await api_client.close()
