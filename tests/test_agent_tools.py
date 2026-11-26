from __future__ import annotations

import httpx
import pytest

from agent.metrics_tracker import TurnLatencyTracker
from agent.tools import ClinicAPIClient
from backend import db
from backend.main import app


@pytest.mark.asyncio
async def test_clinic_api_client_tool_flow(tmp_path, monkeypatch):
    db_file = tmp_path / "agent_tools.db"
    monkeypatch.setenv("CLINIC_DB_PATH", str(db_file))
    db.init_db(db_file, force_reset=True)

    emitted_events = []

    async def capture_event(ev: dict):
        emitted_events.append(ev)

    transport = httpx.ASGITransport(app=app)
    client = ClinicAPIClient(
        base_url="http://testserver",
        transport=transport,
        on_tool_event=capture_event,
    )

    try:
        slots_res = await client.check_available_slots(specialty="Dermatology", date="today")
        assert slots_res["count"] >= 1
        slot_id = slots_res["slots"][0]["id"]

        book_res = await client.book_appointment(
            slot_id=slot_id,
            patient_name="Samira Khan",
            phone="555-0177",
            reason="Skin rash consultation",
        )
        assert book_res["booking_id"].startswith("BK-")
        assert book_res["specialty"] == "Dermatology"

        lookup_res = await client.lookup_appointment(booking_id=book_res["booking_id"])
        assert lookup_res["count"] == 1
        assert lookup_res["appointments"][0]["patient_name"] == "Samira Khan"

        cancel_res = await client.cancel_appointment(booking_id=book_res["booking_id"])
        assert cancel_res["status"] == "cancelled"

        assert len(emitted_events) == 4
        assert all(ev["success"] is True and ev["duration_ms"] >= 0 for ev in emitted_events)
    finally:
        await client.close()


def test_turn_latency_tracker():
    tracker = TurnLatencyTracker()
    tracker.mark_thinking_started(now=10.0)
    tracker.record_eou(0.095)
    tracker.record_stt(0.110)
    tracker.record_llm(ttft_sec=0.130, duration_sec=0.250, tokens_per_second=275.0)
    tracker.record_tool("check_available_slots", duration_ms=12.4, success=True)
    tracker.record_tts(ttfb_sec=0.105, duration_sec=0.450)

    summary = tracker.mark_speaking_started(now=10.355)
    assert summary is not None
    assert summary["turn_id"] == 1
    assert summary["eou_ms"] == 95.0
    assert summary["llm_ttft_ms"] == 130.0
    assert summary["tool_total_ms"] == 12.4
    assert summary["tts_ttfb_ms"] == 105.0
    assert summary["pipeline_sum_ms"] == 342.4
    assert summary["perceived_total_ms"] == 355.0
    assert len(tracker.history) == 1
