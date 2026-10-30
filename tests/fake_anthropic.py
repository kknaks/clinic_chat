"""모의 Anthropic — `messages.stream` · `messages.create` 꼴. 응답은 실물 `anthropic.types.Message` 로 만든다.

policy(params, n) → Turn 으로 바퀴마다 응답을 정한다. 받은 요청 params 는 `calls` 에 남는다.
가짜 시계(FakeClock)를 주면 Turn 의 지연(ms)만큼 시계를 민다.
"""

from __future__ import annotations

import asyncio
import itertools
import json
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
    def __init__(self, turn: Turn, model: str, clock: FakeClock | None, realtime: bool = False) -> None:
        self.turn, self.model, self.clock, self.realtime = turn, model, clock, realtime

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _wait(self, ms: float) -> None:
        if self.clock:
            self.clock.advance(ms)
        await asyncio.sleep(ms / 1000 if self.realtime else 0)

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
    def __init__(self, policy: Callable[[dict, int], Turn] | list[Turn], clock: FakeClock | None = None,
                 realtime: bool = False) -> None:
        self.realtime = realtime
        if isinstance(policy, list):
            seq = list(policy)
            policy = lambda p, n: seq[min(n, len(seq) - 1)]
        self.policy = policy
        self.clock = clock
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream, create=self._create)

    def _next(self, params: dict) -> Turn:
        n = len(self.calls)
        self.calls.append(params)
        return self.policy(params, n)

    def _stream(self, **params: Any) -> _Stream:
        return _Stream(self._next(params), params["model"], self.clock, self.realtime)

    async def _create(self, **params: Any) -> Message:
        t = self._next(params)
        if self.clock:
            self.clock.advance(t.first_ms)
        if self.realtime:
            await asyncio.sleep(t.first_ms / 1000)
        return t.message(params["model"])


def _has_tool_result(params: dict) -> bool:
    for m in params.get("messages", []):
        if m["role"] == "user" and isinstance(m["content"], list) and \
                any(b.get("type") == "tool_result" for b in m["content"]):
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


NAME_POOL = [("소프웨이브", "시술"), ("HIFU 리프팅", "시술"), ("울트라포커스 X", "장비"), ("Nd:YAG", "장비"),
             ("CaHA", "성분"), ("보톡스", "시술"), ("울세라", "시술"), ("턱", "부위")]
GOOD_EV = {"file": "procedure-profiles", "path": "procedure-profiles.M9.aftercare", "quote": "당일 사우나 피하기"}
BAD_EV = {"file": "procedure-profiles", "path": "procedure-profiles.M9.aftercare", "quote": "2주 사우나 금지"}


def _all_text(params: dict) -> str:
    out = []
    for m in params.get("messages", []):
        c = m["content"]
        if isinstance(c, str):
            out.append(c)
        else:
            out += [b.get("text", "") or str(b.get("content", "")) for b in c]
    return "\n".join(out)


def quality_policy(params: dict, n: int) -> Turn:
    """① 이름 뽑기 · ② 채점(모의 Opus)."""
    fmt = (params.get("output_config") or {}).get("format")
    text = _all_text(params)
    if fmt and "names" in fmt["schema"]["properties"]:
        names = [{"text": t, "kind": k} for t, k in NAME_POOL if t in text]
        return Turn(text=[json.dumps({"names": names}, ensure_ascii=False)])
    if fmt:
        answers = []
        for lab in ("A", "B", "C"):
            if f"답 {lab}" not in text:
                continue
            items = [{"item": i, "verdict": "통과", "claim": "", "evidence": None}
                     for i in ("accuracy", "safety", "candidates", "uncertainty", "on_topic")]
            if lab == "A":
                items[0] = {"item": "accuracy", "verdict": "실패", "claim": "fixture 실패", "evidence": GOOD_EV}
            if lab == "B":
                items[1] = {"item": "safety", "verdict": "실패", "claim": "fixture 실패", "evidence": BAD_EV}
            answers.append({"label": lab, "items": items})
        return Turn(text=[json.dumps({"answers": answers}, ensure_ascii=False)])
    if not _has_tool_result(params):
        return Turn(tools=[("raw_get", {"path": "procedure-profiles.M9"})])
    return Turn(text=["확인 끝"])


def mock_policy(params: dict, n: int) -> Turn:
    """모의 판 전체 — 답 모델(Sonnet)은 answer_policy · 그 밖(Opus)은 quality_policy."""
    if params["model"].startswith("claude-sonnet"):
        return answer_policy(params, n)
    return quality_policy(params, n)
