"""속도 측정 — `t0` · `stages[]` 기록기(perf_counter · SPEC-006 §4-1). 시계는 시험에서 바꿔 끼운다."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Clock:
    """세 케이스가 같이 쓰는 `t0` 하나."""

    now: Callable[[], float] = time.perf_counter
    t0: float = 0.0

    def start(self) -> float:
        self.t0 = self.now()
        return self.t0

    def ms(self) -> float:
        return round((self.now() - self.t0) * 1000, 1)


@dataclass
class CaseTimer:
    clock: Clock
    emit_stage: Callable[[dict], Any] | None = None
    stages: list[dict] = field(default_factory=list)
    ttft_ms: float | None = None
    ttft_answer_ms: float | None = None
    total_ms: float | None = None
    llm_round_trips: int = 0

    @contextmanager
    def stage(self, name: str, **extra: Any):
        st = {"name": name, "start_ms": self.clock.ms(), "end_ms": None, **extra}
        self.stages.append(st)
        try:
            yield st
        finally:
            st["end_ms"] = self.clock.ms()
            if self.emit_stage:
                self.emit_stage(dict(st))

    def open_stage(self, name: str, at: float | None = None, **extra: Any) -> dict:
        """스트림 이벤트로 여닫는 단계(CLI) — 열 때도 알림(화면이 지금 무엇을 하는지 바로 그림) · close_stage 로 닫음.
        at = 앞 단계를 닫은 같은 시각(붙은 단계 사이에 틈이 생기지 않게)."""
        st = {"name": name, "start_ms": self.clock.ms() if at is None else at, "end_ms": None, **extra}
        self.stages.append(st)
        self.touch(st)
        return st

    def touch(self, st: dict) -> None:
        """열린 단계의 칸이 바뀜(예: 생각 중) — 다시 알림."""
        if self.emit_stage:
            self.emit_stage(dict(st))

    def close_stage(self, st: dict, at: float | None = None) -> float:
        if st.get("end_ms") is None:
            st["end_ms"] = self.clock.ms() if at is None else at
            if self.emit_stage:
                self.emit_stage(dict(st))
        return st["end_ms"]

    def close_open(self) -> None:
        for st in self.stages:
            if st.get("end_ms") is None:
                self.close_stage(st)

    def mark_text(self) -> float:
        t = self.clock.ms()
        if self.ttft_ms is None:
            self.ttft_ms = t
        return t

    def finish(self) -> None:
        self.total_ms = self.clock.ms()

    def as_dict(self) -> dict:
        return {"ttft_ms": self.ttft_ms, "ttft_answer_ms": self.ttft_answer_ms, "total_ms": self.total_ms,
                "stages": self.stages, "llm_round_trips": self.llm_round_trips}
