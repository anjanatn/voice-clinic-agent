from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_clinic.db"
    monkeypatch.setenv("CLINIC_DB_PATH", str(db_file))
    db.init_db(db_file, force_reset=True)
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_doctors(client: TestClient):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["booked_appointments"] == 2
    assert body["available_slots"] > 0

    doctors = client.get("/api/doctors").json()
    assert len(doctors) == 4
    assert any(d["specialty"] == "Dentistry" for d in doctors)


def test_book_conflict_lookup_reschedule_and_cancel(client: TestClient):
    slots = client.get("/api/slots", params={"specialty": "Dentistry", "date": "tomorrow"}).json()
    assert len(slots) >= 2
    first_slot = slots[0]["id"]
    second_slot = slots[1]["id"]

    # Book slot
    book_resp = client.post(
        "/api/appointments",
        json={
            "slot_id": first_slot,
            "patient_name": "Jordan Lee",
            "phone": "555-0184",
            "reason": "Routine dental cleaning",
        },
    )
    assert book_resp.status_code == 201
    booked = book_resp.json()
    booking_id = booked["booking_id"]
    assert booked["slot_id"] == first_slot
    assert booked["status"] == "confirmed"

    # Double-booking same slot must fail with 409
    conflict_resp = client.post(
        "/api/appointments",
        json={
            "slot_id": first_slot,
            "patient_name": "Someone Else",
            "phone": "555-9999",
            "reason": "Checkup",
        },
    )
    assert conflict_resp.status_code == 409

    # Lookup by phone (with or without dashes)
    lookup_resp = client.get("/api/appointments/lookup", params={"phone": "5550184"})
    assert lookup_resp.status_code == 200
    matches = lookup_resp.json()
    assert len(matches) == 1
    assert matches[0]["booking_id"] == booking_id

    # Reschedule to second slot
    resched_resp = client.patch(
        f"/api/appointments/{booking_id}/reschedule",
        json={"new_slot_id": second_slot},
    )
    assert resched_resp.status_code == 200
    assert resched_resp.json()["slot_id"] == second_slot
    assert resched_resp.json()["status"] == "rescheduled"

    # First slot should now be available again
    open_ids = {s["id"] for s in client.get("/api/slots").json()}
    assert first_slot in open_ids
    assert second_slot not in open_ids

    # Cancel appointment
    cancel_resp = client.delete(f"/api/appointments/{booking_id}")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    open_ids_after = {s["id"] for s in client.get("/api/slots").json()}
    assert second_slot in open_ids_after


def test_livekit_token_unconfigured_and_configured(client: TestClient, monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://your-project.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "your_livekit_api_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "your_livekit_api_secret")

    unconf = client.post("/api/livekit-token", json={"room_name": "r1", "participant_name": "Alice"})
    assert unconf.status_code == 200
    assert unconf.json()["configured"] is False
    assert unconf.json()["token"] == ""

    monkeypatch.setenv("LIVEKIT_URL", "wss://demo.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "devkey1234567890")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret_key_for_livekit_jwt_signing_123456")

    conf = client.post("/api/livekit-token", json={"room_name": "clinic-1", "participant_name": "Alice"})
    assert conf.status_code == 200
    body = conf.json()
    assert body["configured"] is True
    assert len(body["token"]) > 20
