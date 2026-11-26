from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    JobContext,
    RunContext,
    UserInputTranscribedEvent,
    WorkerOptions,
    cli,
    function_tool,
    metrics,
)
from livekit.agents.metrics import EOUMetrics, LLMMetrics, STTMetrics, TTSMetrics
from livekit.agents.voice import MetricsCollectedEvent
from livekit.plugins import cartesia, deepgram, groq, silero

from agent.metrics_tracker import TurnLatencyTracker
from agent.tools import ClinicAPIClient

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")
load_dotenv(ROOT_DIR / ".env.local", override=True)

logger = logging.getLogger("pulsecare-voice-agent")
logger.setLevel(logging.INFO)

TELEMETRY_TOPIC = "pulsecare.telemetry"


def _build_instructions() -> str:
    today = datetime.now().date().isoformat()
    return (
        f"You are Maya, the friendly, ultra-responsive voice receptionist for PulseCare Medical & Dental Clinic. "
        f"Today's date is {today}. "
        "Keep every spoken reply concise (1 to 2 short natural sentences), warm, and easy to understand over voice. "
        "Never use markdown, bullet lists, or asterisks in your replies because your text is spoken aloud. "
        "Our specialties are General Medicine (Dr. Maya Patel), Dentistry (Dr. Elena Rostova), "
        "Dermatology (Dr. Marcus Chen), and Pediatrics (Dr. Priya Nair). "
        "Always check real availability with check_available_slots before promising any time. "
        "Before calling book_appointment, confirm you have the patient's full name, phone number, "
        "reason for visit, and chosen slot ID. "
        "When an appointment is booked or looked up, clearly read out the booking ID (e.g. BK-1044), doctor, date, and time."
    )


class ClinicReceptionistAgent(Agent):
    def __init__(self, api_client: ClinicAPIClient) -> None:
        super().__init__(instructions=_build_instructions())
        self.api_client = api_client

    @function_tool
    async def check_available_slots(
        self,
        context: RunContext,
        specialty: str | None = None,
        doctor_name: str | None = None,
        date: str | None = None,
    ) -> str:
        """Check open clinic appointment slots filtered by specialty, doctor name, or date.

        Args:
            specialty: Medical specialty such as Dentistry, Dermatology, Pediatrics, or General Medicine.
            doctor_name: Optional doctor name, e.g. Dr. Elena Rostova.
            date: Optional date in YYYY-MM-DD format, or 'today' / 'tomorrow'.
        """
        result = await self.api_client.check_available_slots(
            specialty=specialty,
            doctor_name=doctor_name,
            date=date,
        )
        return json.dumps(result)

    @function_tool
    async def book_appointment(
        self,
        context: RunContext,
        slot_id: str,
        patient_name: str,
        phone: str,
        reason: str = "General consultation",
    ) -> str:
        """Book an available clinic slot for a patient.

        Args:
            slot_id: The slot ID returned by check_available_slots, e.g. SLOT-007.
            patient_name: Patient full name.
            phone: Patient phone number.
            reason: Short reason for the visit.
        """
        result = await self.api_client.book_appointment(
            slot_id=slot_id,
            patient_name=patient_name,
            phone=phone,
            reason=reason,
        )
        return json.dumps(result)

    @function_tool
    async def lookup_appointment(
        self,
        context: RunContext,
        booking_id: str | None = None,
        phone: str | None = None,
        patient_name: str | None = None,
    ) -> str:
        """Look up an existing clinic appointment by booking ID, phone number, or patient name.

        Args:
            booking_id: Booking reference such as BK-1042.
            phone: Patient phone number.
            patient_name: Patient name.
        """
        result = await self.api_client.lookup_appointment(
            booking_id=booking_id,
            phone=phone,
            patient_name=patient_name,
        )
        return json.dumps(result)

    @function_tool
    async def reschedule_appointment(
        self,
        context: RunContext,
        booking_id: str,
        new_slot_id: str,
    ) -> str:
        """Move an existing appointment to a new open slot.

        Args:
            booking_id: Existing booking reference such as BK-1042.
            new_slot_id: New slot ID from check_available_slots.
        """
        result = await self.api_client.reschedule_appointment(
            booking_id=booking_id,
            new_slot_id=new_slot_id,
        )
        return json.dumps(result)

    @function_tool
    async def cancel_appointment(
        self,
        context: RunContext,
        booking_id: str,
    ) -> str:
        """Cancel an existing appointment by booking ID and free the time slot.

        Args:
            booking_id: Booking reference to cancel, e.g. BK-1042.
        """
        result = await self.api_client.cancel_appointment(booking_id=booking_id)
        return json.dumps(result)


def _create_tts():
    provider = os.getenv("TTS_PROVIDER", "cartesia").strip().lower()
    if provider == "deepgram":
        return deepgram.TTS(
            model=os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en"),
        )
    return cartesia.TTS(
        model=os.getenv("CARTESIA_MODEL", "sonic-2"),
        voice=os.getenv("CARTESIA_VOICE", "a0e99841-438c-4a64-b679-ae501e7d6091"),
    )


async def entrypoint(ctx: JobContext) -> None:
    logger.info("Connecting PulseCare voice agent to room %s", ctx.room.name)
    await ctx.connect()

    tracker = TurnLatencyTracker()

    async def publish_telemetry(payload: dict[str, Any]) -> None:
        try:
            encoded = json.dumps(payload).encode("utf-8")
            await ctx.room.local_participant.publish_data(
                encoded,
                reliable=True,
                topic=TELEMETRY_TOPIC,
            )
        except Exception as exc:
            logger.debug("Telemetry publish failed: %s", exc)

    async def on_tool_event(event: dict[str, Any]) -> None:
        tracker.record_tool(
            name=event["name"],
            duration_ms=event["duration_ms"],
            success=event["success"],
        )
        await publish_telemetry(event)

    api_client = ClinicAPIClient(on_tool_event=on_tool_event)

    session = AgentSession(
        stt=groq.STT(model=os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")),
        llm=groq.LLM(model=os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")),
        tts=_create_tts(),
        vad=silero.VAD.load(),
    )

    usage_collector = metrics.UsageCollector()

    @session.on("user_input_transcribed")
    def _on_user_transcribed(ev: UserInputTranscribedEvent) -> None:
        asyncio.create_task(
            publish_telemetry(
                {
                    "type": "transcript",
                    "role": "user",
                    "text": ev.transcript,
                    "is_final": ev.is_final,
                }
            )
        )

    @session.on("agent_state_changed")
    def _on_agent_state(ev: AgentStateChangedEvent) -> None:
        new_state = str(ev.new_state)
        if new_state == "thinking":
            tracker.mark_thinking_started()
        elif new_state == "speaking":
            turn_summary = tracker.mark_speaking_started()
            if turn_summary is not None:
                asyncio.create_task(
                    publish_telemetry({"type": "latency_turn", **turn_summary})
                )

        asyncio.create_task(
            publish_telemetry(
                {
                    "type": "agent_state",
                    "old_state": str(ev.old_state),
                    "new_state": new_state,
                }
            )
        )

    @session.on("conversation_item_added")
    def _on_conversation_item(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)
        if callable(text):
            text = text()
        if role == "assistant" and text:
            asyncio.create_task(
                publish_telemetry(
                    {
                        "type": "transcript",
                        "role": "assistant",
                        "text": text,
                        "is_final": True,
                    }
                )
            )

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent) -> None:
        m = ev.metrics
        metrics.log_metrics(m)
        usage_collector.collect(m)

        if isinstance(m, STTMetrics):
            tracker.record_stt(getattr(m, "duration", None))
        elif isinstance(m, EOUMetrics):
            tracker.record_eou(getattr(m, "end_of_utterance_delay", None))
        elif isinstance(m, LLMMetrics):
            tracker.record_llm(
                ttft_sec=getattr(m, "ttft", None),
                duration_sec=getattr(m, "duration", None),
                tokens_per_second=getattr(m, "tokens_per_second", None),
            )
        elif isinstance(m, TTSMetrics):
            tracker.record_tts(
                ttfb_sec=getattr(m, "ttfb", None),
                duration_sec=getattr(m, "duration", None),
            )

    async def _shutdown() -> None:
        summary = usage_collector.get_summary()
        logger.info("PulseCare Session Usage Summary: %s", summary)
        await api_client.close()

    ctx.add_shutdown_callback(_shutdown)

    agent = ClinicReceptionistAgent(api_client=api_client)
    await session.start(room=ctx.room, agent=agent)

    await session.generate_reply(
        instructions="Greet the caller warmly as Maya from PulseCare Clinic and ask how you can help with appointments today."
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
