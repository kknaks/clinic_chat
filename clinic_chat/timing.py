"""시간 — 한 열 실행의 단계 시간(SPEC-002 §7-2 `timing{ttft_ms, total_ms, stages[]}`)."""

from __future__ import annotations

import time
from contextlib import contextmanager


class Clock:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def ms(self) -> float:
        return round((time.perf_counter() - self.t0) * 1000, 1)


class Timer:
    """단계 `{name, start_ms, end_ms}` 를 모은다.

    `ttft_ms` = 첫 글자(도구 사이 메모 포함 — LLM 쪽이 `mark_text` 로 알림) · `first_answer_ms` = 첫 **답** 글자
    (마지막 도구 호출 뒤 첫 글자 — 도구 호출 때 `new_segment` 로 다시 잰다 · 도구가 없으면 `ttft_ms` 와 같다)."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or Clock()
        self.stages: list[dict] = []
        self.ttft_ms: float | None = None
        self.first_answer_ms: float | None = None
        self.total_ms: float | None = None

    def open(self, name: str) -> dict:
        st = {"name": name, "start_ms": self.clock.ms(), "end_ms": None}
        self.stages.append(st)
        return st

    def close(self, st: dict) -> None:
        if st.get("end_ms") is None:
            st["end_ms"] = self.clock.ms()

    @contextmanager
    def stage(self, name: str):
        st = self.open(name)
        try:
            yield st
        finally:
            self.close(st)

    def mark_text(self) -> float:
        ms = self.clock.ms()
        if self.ttft_ms is None:
            self.ttft_ms = ms
        if self.first_answer_ms is None:
            self.first_answer_ms = ms
        return ms

    def new_segment(self) -> None:
        """도구 호출 — 그 앞 글은 메모였다(첫 답 글자를 다음 글에서 다시 잰다)."""
        self.first_answer_ms = None

    def finish(self) -> None:
        for st in self.stages:
            self.close(st)
        self.total_ms = self.clock.ms()

    def as_dict(self) -> dict:
        return {"ttft_ms": self.ttft_ms, "first_answer_text_ms": self.first_answer_ms, "total_ms": self.total_ms,
                "stages": list(self.stages)}
