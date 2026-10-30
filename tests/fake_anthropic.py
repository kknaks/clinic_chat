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
    if fmt:  # 절대 채점(답 하나) — 답에 「원장」이 있으면 정확성 실패(맞는 근거) · 「골드」면 안전 실패(없는 근거)
        items = [{"item": i, "verdict": "통과", "claim": "", "evidence": None}
                 for i in ("accuracy", "safety", "candidates", "uncertainty", "on_topic")]
        if "원장" in text or "답 하나" in text:
            items[0] = {"item": "accuracy", "verdict": "실패", "claim": "fixture 실패", "evidence": GOOD_EV}
        if "골드" in text or "답 둘" in text:
            items[1] = {"item": "safety", "verdict": "실패", "claim": "fixture 실패", "evidence": BAD_EV}
        return Turn(text=[json.dumps({"items": items}, ensure_ascii=False)])
    if not _has_tool_result(params):
        return Turn(tools=[("raw_get", {"path": "procedure-profiles.M9"})])
    return Turn(text=["확인 끝"])


def mock_policy(params: dict, n: int) -> Turn:
    """모의 판 전체 — 답 모델(Sonnet)은 answer_policy · 그 밖(Opus)은 quality_policy."""
    if params["model"].startswith("claude-sonnet"):
        return answer_policy(params, n)
    return quality_policy(params, n)


# ───── 토큰 · 캐시 · 지연 어림(비용 추정용 — tests/estimate_cost.py) ─────
BYTES_PER_TOKEN = 3.5  # 어림(한국어 · JSON 섞임) — 실물 usage 로 다시 맞춤
MIN_CACHE_TOKENS = 512  # Sonnet 5.5 · Opus 5.5 최소 캐시 길이(claude-api 스킬 prompt-caching)


def est_tokens(s: str, bpt: float | None = None) -> int:
    return max(1, int(len(s.encode("utf-8")) / (bpt or BYTES_PER_TOKEN)))


class EstimatingAnthropic(FakeAnthropic):
    """요청 크기로 usage 를 어림 — cache_control 이 있으면 앞 요청과 같은 앞부분을 캐시 읽기로 셈.
    지연: 첫 이벤트 = 300ms + 캐시 안 된 입력 1k 토큰당 40ms + 캐시 읽기 1k 당 4ms · 출력 델타마다 15ms."""

    def __init__(self, policy, clock=None):
        super().__init__(policy, clock=clock)
        self.cache: list[str] = []
        self.log: list[dict] = []

    def _next(self, params: dict) -> Turn:
        t = super()._next(params)
        head = json.dumps(params.get("tools", []), ensure_ascii=False) + json.dumps(params.get("system"), ensure_ascii=False)
        # 메시지마다 이어 붙임(앞 요청이 그대로 앞부분이 되도록 — 배열 닫는 괄호 없이)
        full = head + "".join(json.dumps(m, ensure_ascii=False) for m in params.get("messages") or [])
        total = est_tokens(full)
        read_s = max((c for c in self.cache if full.startswith(c)), key=len, default="")
        read = est_tokens(read_s) if read_s else 0
        sys_marked = isinstance(params.get("system"), list) and any("cache_control" in b for b in params["system"])
        write = 0
        if params.get("cache_control"):
            if total >= MIN_CACHE_TOKENS:
                write, _ = total - read, self.cache.append(full)
        elif sys_marked and est_tokens(head) >= MIN_CACHE_TOKENS and head not in self.cache:
            write, _ = est_tokens(head) - read, self.cache.append(head)
        write = max(write, 0)
        out = est_tokens("".join(t.text) + json.dumps(t.tools, ensure_ascii=False)) if (t.text or t.tools) else 1
        t.usage = {"input_tokens": max(total - read - write, 0), "output_tokens": out,
                   "cache_creation_input_tokens": write, "cache_read_input_tokens": read}
        t.first_ms = 300 + (total - read) / 1000 * 40 + read / 1000 * 4
        t.chunk_ms = 15
        self.log.append({"model": params["model"], **t.usage})
        return t
