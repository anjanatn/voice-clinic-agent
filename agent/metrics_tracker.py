from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field


@dataclass
class ToolTiming:
    name: str
    duration_ms: float
    success: bool
    summary: str = ""


@dataclass
class TurnLatencySnapshot:
    turn_id: int
    stt_ms: float | None = None
    eou_ms: float | None = None
    llm_ttft_ms: float | None = None
    llm_duration_ms: float | None = None
    tokens_per_sec: float | None = None
    tts_ttfb_ms: float | None = None
    tts_duration_ms: float | None = None
    tool_total_ms: float = 0.0
    tools: list[ToolTiming] = field(default_factory=list)
    perceived_total_ms: float | None = None
    pipeline_sum_ms: float | None = None

    def finalize(self) -> dict:
        components = [
            v
            for v in (self.eou_ms, self.llm_ttft_ms, self.tts_ttfb_ms)
            if v is not None
        ]
        if self.tool_total_ms > 0:
            components.append(self.tool_total_ms)
        self.pipeline_sum_ms = round(sum(components), 1) if components else None
        return {
            "turn_id": self.turn_id,
            "stt_ms": _round_opt(self.stt_ms),
            "eou_ms": _round_opt(self.eou_ms),
            "llm_ttft_ms": _round_opt(self.llm_ttft_ms),
            "llm_duration_ms": _round_opt(self.llm_duration_ms),
            "tokens_per_sec": _round_opt(self.tokens_per_sec),
            "tts_ttfb_ms": _round_opt(self.tts_ttfb_ms),
            "tts_duration_ms": _round_opt(self.tts_duration_ms),
            "tool_total_ms": round(self.tool_total_ms, 1),
            "tools": [asdict(t) for t in self.tools],
            "perceived_total_ms": _round_opt(self.perceived_total_ms),
            "pipeline_sum_ms": self.pipeline_sum_ms,
        }


def _round_opt(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


class TurnLatencyTracker:
    """Tracks per-turn voice pipeline latencies from end-of-user-speech to first audio."""

    def __init__(self) -> None:
        self._turn_counter = 0
        self._current: TurnLatencySnapshot | None = None
        self._thinking_started_at: float | None = None
        self._history: list[dict] = []

    @property
    def current_turn(self) -> TurnLatencySnapshot:
        if self._current is None:
            self._turn_counter += 1
            self._current = TurnLatencySnapshot(turn_id=self._turn_counter)
        return self._current

    def start_new_turn(self) -> TurnLatencySnapshot:
        self._turn_counter += 1
        self._current = TurnLatencySnapshot(turn_id=self._turn_counter)
        self._thinking_started_at = None
        return self._current

    def mark_thinking_started(self, now: float | None = None) -> None:
        self.current_turn
        self._thinking_started_at = now if now is not None else time.perf_counter()

    def mark_speaking_started(self, now: float | None = None) -> dict | None:
        if self._current is None:
            return None
        ts = now if now is not None else time.perf_counter()
        if self._thinking_started_at is not None and self._current.perceived_total_ms is None:
            self._current.perceived_total_ms = (ts - self._thinking_started_at) * 1000.0
        return self.flush_turn()

    def record_stt(self, duration_sec: float | None) -> None:
        if duration_sec is not None and duration_sec >= 0:
            self.current_turn.stt_ms = duration_sec * 1000.0

    def record_eou(self, delay_sec: float | None) -> None:
        if delay_sec is not None and delay_sec >= 0:
            self.current_turn.eou_ms = delay_sec * 1000.0

    def record_llm(
        self,
        ttft_sec: float | None,
        duration_sec: float | None = None,
        tokens_per_second: float | None = None,
    ) -> None:
        turn = self.current_turn
        if ttft_sec is not None and ttft_sec >= 0:
            turn.llm_ttft_ms = ttft_sec * 1000.0
        if duration_sec is not None and duration_sec >= 0:
            turn.llm_duration_ms = duration_sec * 1000.0
        if tokens_per_second is not None and tokens_per_second >= 0:
            turn.tokens_per_sec = tokens_per_second

    def record_tts(self, ttfb_sec: float | None, duration_sec: float | None = None) -> None:
        turn = self.current_turn
        if ttfb_sec is not None and ttfb_sec >= 0:
            turn.tts_ttfb_ms = ttfb_sec * 1000.0
        if duration_sec is not None and duration_sec >= 0:
            turn.tts_duration_ms = duration_sec * 1000.0

    def record_tool(
        self,
        name: str,
        duration_ms: float,
        success: bool = True,
        summary: str = "",
    ) -> None:
        turn = self.current_turn
        rounded = round(duration_ms, 1)
        turn.tools.append(ToolTiming(name=name, duration_ms=rounded, success=success, summary=summary))
        turn.tool_total_ms = round(sum(t.duration_ms for t in turn.tools), 1)

    def flush_turn(self) -> dict | None:
        if self._current is None:
            return None
        snapshot = self._current.finalize()
        self._history.append(snapshot)
        self._current = None
        self._thinking_started_at = None
        return snapshot

    @property
    def history(self) -> list[dict]:
        return list(self._history)
