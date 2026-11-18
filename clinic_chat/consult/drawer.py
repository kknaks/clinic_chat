"""드로어 색인(SPEC-006 §8A SV2 · SV3 · SV5 · WORK-014 §6a · 코디 W-11 · 검수 N-9).

- 서버가 뜰 때(`Ledger` 를 세울 때) 장부를 **한 번** 훑어 메모리 색인을 세우고, 시작 줄 · 맺는 줄을 쓸 때 함께 고친다.
  목록을 그릴 때 장부 파일을 다시 읽지 않는다.
- 항목 = `{conv_id, first_question, first_asked_at, last_asked_at, n_turns, state}` · 순서 = 마지막 턴 `asked_at` 내림차순.
- 시작 줄이 하나도 없는 대화(`GET /` 가 만든 빈 대화)는 목록에 없다. 시작 줄만 있고 맺는 줄이 없는 대화(턴 도중 죽음)는
  첫 질문이 있으므로 나온다 — `state` = `broken`(B-6).
- `state`(글 없음 · `data-conv-state`) = `open` · `broken` · `build_changed` · `turn_limit` · `outdated`(서버 값과 대화 값이
  다름 — FX3) — 계산은 그릴 때(서버 값을 받는다).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Entry:
    conv_id: str
    first_question: str = ""
    first_asked_at: str = ""
    last_asked_at: str = ""
    n_turns: int = 0  # 시작한 턴 수
    answered: int = 0  # CLI 가 답한 턴 수(CL13)
    open: set[int] = field(default_factory=set)  # 맺는 줄이 아직 없는 턴
    ended: str | None = None  # 맺는 줄 `conv_end`(broken · build_changed) 또는 세션 broken
    consult_state: str | None = None
    consult_rev: int | None = None
    dead: bool = False  # 훑을 때 맺는 줄 없는 턴이 있었다(서버가 턴 도중 죽음)


class DrawerIndex:
    def __init__(self) -> None:
        self.items: dict[str, Entry] = {}

    @classmethod
    def scan(cls, path: Path) -> DrawerIndex:
        ix = cls()
        if path.is_file():
            for ln in path.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("phase") == "start":
                    ix.on_start(r)
                elif r.get("phase") == "end":
                    ix.on_end(r)
        for e in ix.items.values():
            if e.open:
                e.dead = True
        return ix

    def on_start(self, r: dict) -> None:
        cid = str(r.get("conv_id") or "")
        if not cid:
            return
        e = self.items.get(cid)
        if e is None:
            e = self.items[cid] = Entry(cid, str(r.get("question") or ""), str(r.get("asked_at") or ""),
                                        consult_state=r.get("consult_state") or "off",
                                        consult_rev=r.get("consult_rev"))
        e.last_asked_at = str(r.get("asked_at") or e.last_asked_at)
        e.n_turns += 1
        if r.get("turn") is not None:
            e.open.add(int(r["turn"]))

    def on_end(self, r: dict) -> None:
        e = self.items.get(str(r.get("conv_id") or ""))
        if e is None:
            return
        if r.get("turn") is not None:
            e.open.discard(int(r["turn"]))
        if (r.get("llm") or {}).get("answered"):
            e.answered += 1
        if r.get("conv_end") in ("broken", "build_changed"):
            e.ended = r["conv_end"]
        elif r.get("session_state") == "broken":
            e.ended = "broken"

    def state(self, e: Entry, *, server_state: str, server_rev: int, max_turns: int) -> str:
        if e.ended:
            return e.ended
        if e.dead and e.open:
            return "broken"
        if outdated(e.consult_state, e.consult_rev, server_state=server_state, server_rev=server_rev):
            return "outdated"
        if e.answered >= max_turns:
            return "turn_limit"
        return "open"

    def listing(self, limit: int, *, server_state: str, server_rev: int, max_turns: int) -> list[dict]:
        es = sorted(self.items.values(), key=lambda e: (e.last_asked_at, e.first_asked_at), reverse=True)[:max(0, limit)]
        return [{"conv_id": e.conv_id, "first_question": e.first_question, "first_asked_at": e.first_asked_at,
                 "last_asked_at": e.last_asked_at, "n_turns": e.n_turns,
                 "state": self.state(e, server_state=server_state, server_rev=server_rev, max_turns=max_turns)}
                for e in es]


def outdated(conv_state: str | None, conv_rev: int | None, *, server_state: str, server_rev: int) -> bool:
    """FX3 — 서버 켬 · 대화 끔 / 켠 대화에 `consult_rev` 없음 · 작음."""
    if conv_state is None:
        return False
    if server_state == "on" and conv_state != "on":
        return True
    return conv_state == "on" and (conv_rev is None or conv_rev < server_rev)
