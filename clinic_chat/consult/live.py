"""턴 하나의 줄기(SPEC-004 §4-3) — 시험 화면 `web/server.py` 의 `Live` · `to_sse` · `resume_from` 꼴을 따른다(그 파일은 안 고침).

자료는 HTML 조각이다 — 줄바꿈은 SSE `data:` 줄 여럿으로. 조각 글자 길이는 늘 > 0 이다(자료 없는 이벤트를 브라우저가
버리므로 — 검수 W-3). 줄기 닫기 = `done` · `turn_error` 뒤 `close`(자료 줄 있음 · `sse-close="close"`).
턴은 구독자와 무관하게 끝까지 돈다(W-6) — 줄기는 `Live` 를 읽기만 한다.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

CLOSE = "close"
CLOSE_DATA = "close"


@dataclass
class Live:
    events: list[tuple[str, str]] = field(default_factory=list)
    finished: bool = False
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def push(self, kind: str, html: str) -> None:
        if not html:
            raise ValueError(kind)  # W-3 ⑤ — 빈 자료는 브라우저가 버린다
        self.events.append((kind, html))
        self.wake.set()

    def finish(self) -> None:
        if not self.events or self.events[-1][0] != CLOSE:
            self.events.append((CLOSE, CLOSE_DATA))
        self.finished = True
        self.wake.set()

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]


def to_sse(kind: str, html: str, i: int) -> str:
    """`id:` = 이벤트 번호(다시 붙기용) · 자료 줄마다 `data:`."""
    lines = html.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return f"id: {i}\nevent: {kind}\n" + "".join(f"data: {ln}\n" for ln in lines) + "\n"


def resume_from(last_event_id: str | None) -> int:
    try:
        return max(0, int(last_event_id) + 1) if last_event_id is not None else 0
    except ValueError:
        return 0


async def stream(lv: Live, start: int = 0, keepalive_s: float = 15):
    i = start
    while True:
        lv.wake.clear()
        while i < len(lv.events):
            yield to_sse(*lv.events[i], i)
            i += 1
        if lv.finished and i >= len(lv.events):
            return
        try:
            await asyncio.wait_for(lv.wake.wait(), timeout=keepalive_s)
        except TimeoutError:
            yield ": keep-alive\n\n"
