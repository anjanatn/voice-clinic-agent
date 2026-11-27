"""Cross-platform latency benchmark for PulseCare FastAPI + SQLite tools."""
from __future__ import annotations

import asyncio
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from agent.tools import ClinicAPIClient


def _summarize(label: str, samples_ms: list[float]) -> None:
    ordered = sorted(samples_ms)
    n = len(ordered)
    p50 = statistics.median(ordered)
    p95 = ordered[min(n - 1, int(0.95 * (n - 1)))]
    mean = statistics.mean(ordered)
    print(
        f"{label:28s} | mean={mean:6.2f} ms | p50={p50:6.2f} ms | "
        f"p95={p95:6.2f} ms | min={ordered[0]:5.2f} ms | max={ordered[-1]:5.2f} ms"
    )


async def run_benchmark(base_url: str) -> None:
    timings: dict[str, list[float]] = {
        "check_available_slots": [],
        "book_appointment": [],
        "lookup_appointment": [],
        "reschedule_appointment": [],
        "cancel_appointment": [],
    }

    async def on_event(ev: dict) -> None:
        if ev["name"] in timings:
            timings[ev["name"]].append(ev["duration_ms"])

    client = ClinicAPIClient(base_url=base_url, on_tool_event=on_event)
    try:
        # Warm up persistent HTTPX keep-alive pool
        await client.check_available_slots(specialty="Dentistry", date="tomorrow")
        timings["check_available_slots"].clear()

        slots_res = await client.check_available_slots(specialty="Dentistry")
        slot_ids = [s["id"] for s in slots_res["slots"]]
        for _ in range(19):
            await client.check_available_slots(specialty="Dentistry", date="tomorrow")

        # Book, lookup, reschedule, cancel loop over live TCP + SQLite
        open_all = (await client.check_available_slots())["slots"]
        timings["check_available_slots"].pop()
        if len(open_all) < 2 and len(slot_ids) >= 2:
            open_all = slots_res["slots"]

        for i in range(15):
            all_open = (await client.check_available_slots())["slots"]
            timings["check_available_slots"].pop()
            s1, s2 = all_open[0]["id"], all_open[1]["id"]
            booked = await client.book_appointment(
                slot_id=s1,
                patient_name=f"Bench Patient {i}",
                phone=f"555-8{i:03d}",
                reason="Latency benchmark",
            )
            bid = booked["booking_id"]
            await client.lookup_appointment(booking_id=bid)
            await client.reschedule_appointment(booking_id=bid, new_slot_id=s2)
            await client.cancel_appointment(booking_id=bid)

        print("\nLive Loopback TCP + FastAPI + SQLite Tool Latency (HTTPX keep-alive):")
        for name, samples in timings.items():
            if samples:
                _summarize(name, samples)
    finally:
        await client.close()


def main() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="pulsecare_bench_"))
    db_path = tmp_dir / "bench.db"
    env = os.environ.copy()
    env["CLINIC_DB_PATH"] = str(db_path)

    import httpx

    port = 8766
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base_url = f"http://127.0.0.1:{port}"
        for _ in range(40):
            try:
                if httpx.get(f"{base_url}/api/health", timeout=0.5).status_code == 200:
                    break
            except Exception:
                time.sleep(0.25)
        asyncio.run(run_benchmark(base_url))
    finally:
        proc.terminate()
        proc.wait(timeout=5)


if __name__ == "__main__":
    main()
