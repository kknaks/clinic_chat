"""단계 ② — 허브 읽기(결정론 · Jev 없음) · 방문 장부(SPEC-002 §3-1 단계 ② · §3-5 · G9).

고른 대상 × 읽기로 한 종류의 조회마다 `next_cursor: null` 까지 · 쪽 밖 목록 포함 · 읽은 것 **전부** `verified` 에 ·
같은 키를 두 번 읽지 않는다. 끝까지 못 읽은 조회가 하나라도 있으면 「실행 중단」.
판정용(`end_state` — §5 (가)) · 표지용(`label` — N-1) 조회는 읽되 `verified` 에 넣지 않는다(K5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clinic_chat.clients.hub_rest import HubRest, ReadAll, collect_gids
from clinic_chat.walk.calls import Call, followup_calls
from clinic_chat.walk.verified import Verified

NOT_VERIFIED = ("end_state", "label")


@dataclass
class Ledger:
    """방문 장부 — 키 = (build, 노드, 도구 · 표, facet, cursor). 한 질문 안의 것."""

    visited: list[dict] = field(default_factory=list)
    calls_done: dict[str, int] = field(default_factory=dict)  # Call.key() → reads 순번

    def seen(self, call: Call) -> int | None:
        return self.calls_done.get(call.key())

    def record(self, call: Call, ra: ReadAll, no: int) -> None:
        self.calls_done[call.key()] = no
        gh = (ra.build or {}).get("gold_hash")
        for cur in ra.cursors or [None]:
            self.visited.append({"build": gh, "node": call.node(), "tool": call.tool,
                                 "facet": call.request.get("facet"), "cursor": cur, "purpose": call.purpose,
                                 "range": call.range, "kind": list(call.kinds)})


@dataclass
class Read:
    call: Call
    ra: ReadAll
    gids: list[str]

    def trace(self) -> dict:
        return {**self.ra.summary(), "range": list(self.call.ranges), "kind": list(self.call.kinds),
                "purpose": self.call.purpose, "candidate": self.call.candidate,
                **({"followup": True} if self.call.meta.get("followup") else {}),
                **({k: self.call.meta[k] for k in ("mark", "widened", "from_risk", "value_pick") if self.call.meta.get(k)}),
                **({"label_for": self.call.meta["label_for"]} if self.call.meta.get("label_for") else {})}


@dataclass
class BreadthError(RuntimeError):
    reason: str
    read: Read | None = None

    def __str__(self) -> str:
        return self.reason


async def read_calls(hub: HubRest, calls: list[Call], verified: Verified, ledger: Ledger, reads: list[Read], *,
                     depth: str = "breadth", read_as_by_node: dict[str, dict] | None = None,
                     follow: bool = True) -> list[Read]:
    """조회 목록을 끝까지 읽는다. `read` · `depth` 는 verified 에 · 결정론 이어 조회(followup)도 같은 규칙으로 끝까지."""
    queue = list(calls)
    new: list[Read] = []
    while queue:
        c = queue.pop(0)
        prev = ledger.seen(c)
        if prev is not None:
            p = reads[prev].call
            if c.purpose not in NOT_VERIFIED and p.purpose in NOT_VERIFIED:
                pass  # 앞서 판정용으로만 읽은 조회를 읽기로 다시 — 아래에서 다시 읽는다(드문 꼴)
            else:
                if c.purpose not in NOT_VERIFIED:  # 같은 조회가 다른 종류 · 범위에서도 나옴 — 행에 둘 다 적는다(RG7)
                    p.kinds = tuple(dict.fromkeys((*p.kinds, *c.kinds)))
                    p.ranges = tuple(dict.fromkeys((*p.ranges, *c.ranges)))
                    verified.add_response(reads[prev].ra.as_response(), ranges=c.ranges, kinds=c.kinds,
                                          depth=depth, call_no=prev, followup=bool(c.meta.get("followup")),
                                          mark=c.meta.get("mark"))
                continue
        ra = await hub.read_all(c.tool, c.request)
        no = len(reads)
        r = Read(c, ra, collect_gids(ra.as_response()))
        reads.append(r)
        new.append(r)
        ledger.record(c, ra, no)
        if not ra.complete:
            raise BreadthError(f"G9: {c.tool} 끝까지 못 읽음 — {ra.error}", r)
        if c.purpose in NOT_VERIFIED:
            continue
        read_as = (read_as_by_node or {}).get(c.node())
        verified.add_response(ra.as_response(), ranges=c.ranges, kinds=c.kinds, depth=depth, call_no=no,
                              read_as=read_as, followup=bool(c.meta.get("followup")), mark=c.meta.get("mark"))
        if follow:
            for f in followup_calls(c, ra.as_response()):
                if depth != "breadth":
                    f.purpose = "depth"
                queue.append(f)
    return new
