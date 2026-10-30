"""모의 Anthropic — `messages.stream` · `messages.create` 꼴. 응답은 실물 `anthropic.types.Message` 로 만든다.

policy(params, n) → Turn 으로 바퀴마다 응답을 정한다. 받은 요청 params 는 `calls` 에 남는다.
가짜 시계(FakeClock)를 주면 Turn 의 지연(ms)만큼 시계를 민다.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from anthropic.types import Message

_ids = itertools.count(1)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def now(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000


@dataclass
class Turn:
    text: list[str] = field(default_factory=list)  # 텍스트 델타
    tools: list[tuple[str, dict]] = field(default_factory=list)  # tool_use (이름, 입력)
    stop_reason: str | None = None
    thinking: str | None = None
    first_ms: float = 0  # 첫 이벤트 전 지연
    chunk_ms: float = 0  # 델타 사이 지연
    usage: dict = field(default_factory=lambda: {"input_tokens": 100, "output_tokens": 20})

    def message(self, model: str) -> Message:
        content: list[dict] = []
        if self.thinking is not None:
            content.append({"type": "thinking", "thinking": self.thinking, "signature": "fixture-sig"})
        if self.text:
            content.append({"type": "text", "text": "".join(self.text)})
        for name, inp in self.tools:
            content.append({"type": "tool_use", "id": f"toolu_fx{next(_ids)}", "name": name, "input": inp})
        stop = self.stop_reason or ("tool_use" if self.tools else "end_turn")
        return Message.model_validate({"id": f"msg_fx{next(_ids)}", "type": "message", "role": "assistant",
                                       "model": model, "content": content, "stop_reason": stop,
                                       "stop_sequence": None, "usage": self.usage})


class _Stream:
    def __init__(self, turn: Turn, model: str, clock: FakeClock | None) -> None:
        self.turn, self.model, self.clock = turn, model, clock

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _wait(self, ms: float) -> None:
        if self.clock:
            self.clock.advance(ms)
        await asyncio.sleep(0)

    async def __aiter__(self):
        await self._wait(self.turn.first_ms)
        if self.turn.thinking is not None:
            yield SimpleNamespace(type="thinking", thinking=self.turn.thinking)
            await self._wait(self.turn.chunk_ms)
        for t in self.turn.text:
            yield SimpleNamespace(type="text", text=t)
            await self._wait(self.turn.chunk_ms)

    async def get_final_message(self) -> Message:
        return self.turn.message(self.model)


class FakeAnthropic:
    def __init__(self, policy: Callable[[dict, int], Turn] | list[Turn], clock: FakeClock | None = None) -> None:
        if isinstance(policy, list):
            seq = list(policy)
            policy = lambda p, n: seq[min(n, len(seq) - 1)]  # noqa: E731
        self.policy = policy
        self.clock = clock
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream, create=self._create)

    def _next(self, params: dict) -> Turn:
        n = len(self.calls)
        self.calls.append(params)
        return self.policy(params, n)

    def _stream(self, **params: Any) -> _Stream:
        return _Stream(self._next(params), params["model"], self.clock)

    async def _create(self, **params: Any) -> Message:
        t = self._next(params)
        if self.clock:
            self.clock.advance(t.first_ms)
        return t.message(params["model"])


def _has_tool_result(params: dict) -> bool:
    for m in params.get("messages", []):
        if m["role"] == "user" and isinstance(m["content"], list):
            if any(b.get("type") == "tool_result" for b in m["content"]):
                return True
    return False


ANSWERS = {
    "case1": ["fixture 원장 답: ", "소프웨이브는 ", "초음파 리프팅입니다. ", "임신 중에는 금기입니다."],
    "case2": ["fixture 골드 답: ", "소프웨이브(", "HIFU 리프팅)는 ", "부작용 정보는 「정보 없음」입니다."],
    "case3": ["fixture 경로 답: ", "소프웨이브와 ", "울트라포커스 X ", "를 권합니다."],
}


def case_of(params: dict) -> str:
    names = {t["name"] for t in params.get("tools", [])}
    if "raw_search" in names:
        return "case1"
    if "resolve" in names:
        return "case2"
    return "case3"


def answer_policy(params: dict, n: int) -> Turn:
    """세 케이스 답(모의 판 · 시험) — 도구가 있으면 한 번 부르고 답한다."""
    c = case_of(params)
    if c != "case3" and not _has_tool_result(params):
        q = params["messages"][0]["content"]
        call = ("raw_search", {"query": "소프웨이브"}) if c == "case1" else ("resolve", {"text": q[:200]})
        return Turn(tools=[call], first_ms=5)
    return Turn(text=ANSWERS[c], first_ms=5, chunk_ms=5)
