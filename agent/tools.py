from __future__ import annotations

import os
import time
from typing import Any, Awaitable, Callable

import httpx

ToolEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


class ClinicAPIClient:
    """Persistent HTTPX client talking to the FastAPI clinic service with per-call timing."""

    def __init__(
        self,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        on_tool_event: ToolEventCallback | None = None,
    ) -> None:
        resolved_url = base_url or os.getenv("CLINIC_API_URL", "http://127.0.0.1:8000")
        self._client = httpx.AsyncClient(
            base_url=resolved_url,
            timeout=8.0,
            transport=transport,
        )
        self._on_tool_event = on_tool_event

    async def close(self) -> None:
        await self._client.aclose()

    async def _emit(
        self,
        name: str,
        args: dict[str, Any],
        duration_ms: float,
        success: bool,
        result: Any,
    ) -> None:
        if self._on_tool_event is None:
            return
        await self._on_tool_event(
            {
                "type": "tool_call",
                "name": name,
                "args": args,
                "duration_ms": round(duration_ms, 1),
                "success": success,
                "result": result,
            }
        )

    async def check_available_slots(
        self,
        specialty: str | None = None,
        doctor_name: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {}
        if specialty:
            params["specialty"] = specialty
        if doctor_name:
            params["doctor_name"] = doctor_name
        if date:
            params["date"] = date

        t0 = time.perf_counter()
        try:
            resp = await self._client.get("/api/slots", params=params)
            resp.raise_for_status()
            slots = resp.json()
            duration_ms = (time.perf_counter() - t0) * 1000.0
            payload = {"count": len(slots), "slots": slots[:8]}
            await self._emit("check_available_slots", params, duration_ms, True, payload)
            return payload
        except httpx.HTTPError as exc:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            err = {"error": str(exc)}
            await self._emit("check_available_slots", params, duration_ms, False, err)
            return err

    async def book_appointment(
        self,
        slot_id: str,
        patient_name: str,
        phone: str,
        reason: str = "General consultation",
    ) -> dict[str, Any]:
        body = {
            "slot_id": slot_id,
            "patient_name": patient_name,
            "phone": phone,
            "reason": reason,
        }
        t0 = time.perf_counter()
        try:
            resp = await self._client.post("/api/appointments", json=body)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code >= 400:
                detail = resp.json().get("detail", resp.text)
                err = {"error": detail, "status_code": resp.status_code}
                await self._emit("book_appointment", body, duration_ms, False, err)
                return err
            data = resp.json()
            await self._emit("book_appointment", body, duration_ms, True, data)
            return data
        except httpx.HTTPError as exc:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            err = {"error": str(exc)}
            await self._emit("book_appointment", body, duration_ms, False, err)
            return err

    async def lookup_appointment(
        self,
        booking_id: str | None = None,
        phone: str | None = None,
        patient_name: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, str] = {}
        if booking_id:
            params["booking_id"] = booking_id
        if phone:
            params["phone"] = phone
        if patient_name:
            params["patient_name"] = patient_name

        t0 = time.perf_counter()
        try:
            resp = await self._client.get("/api/appointments/lookup", params=params)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code >= 400:
                detail = resp.json().get("detail", resp.text)
                err = {"error": detail}
                await self._emit("lookup_appointment", params, duration_ms, False, err)
                return err
            matches = resp.json()
            payload = {"count": len(matches), "appointments": matches}
            await self._emit("lookup_appointment", params, duration_ms, True, payload)
            return payload
        except httpx.HTTPError as exc:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            err = {"error": str(exc)}
            await self._emit("lookup_appointment", params, duration_ms, False, err)
            return err

    async def reschedule_appointment(
        self,
        booking_id: str,
        new_slot_id: str,
    ) -> dict[str, Any]:
        body = {"new_slot_id": new_slot_id}
        t0 = time.perf_counter()
        try:
            resp = await self._client.patch(f"/api/appointments/{booking_id}/reschedule", json=body)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code >= 400:
                detail = resp.json().get("detail", resp.text)
                err = {"error": detail, "status_code": resp.status_code}
                await self._emit(
                    "reschedule_appointment",
                    {"booking_id": booking_id, "new_slot_id": new_slot_id},
                    duration_ms,
                    False,
                    err,
                )
                return err
            data = resp.json()
            await self._emit(
                "reschedule_appointment",
                {"booking_id": booking_id, "new_slot_id": new_slot_id},
                duration_ms,
                True,
                data,
            )
            return data
        except httpx.HTTPError as exc:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            err = {"error": str(exc)}
            await self._emit(
                "reschedule_appointment",
                {"booking_id": booking_id, "new_slot_id": new_slot_id},
                duration_ms,
                False,
                err,
            )
            return err

    async def cancel_appointment(self, booking_id: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            resp = await self._client.delete(f"/api/appointments/{booking_id}")
            duration_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code >= 400:
                detail = resp.json().get("detail", resp.text)
                err = {"error": detail, "status_code": resp.status_code}
                await self._emit("cancel_appointment", {"booking_id": booking_id}, duration_ms, False, err)
                return err
            data = resp.json()
            await self._emit("cancel_appointment", {"booking_id": booking_id}, duration_ms, True, data)
            return data
        except httpx.HTTPError as exc:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            err = {"error": str(exc)}
            await self._emit("cancel_appointment", {"booking_id": booking_id}, duration_ms, False, err)
            return err
