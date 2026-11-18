"""단계 ①-4 입구 고르기 · 상품 이름 길(SPEC-002 §3-1 입구 고르기 표 · BR3).

- **입구 고르기는 후보가 0 일 때만 돈다**(TG4 · CD4) — 읽기로 한 범위(들)의 입구 목록 전부(쪽 넘김 끝까지)를 개념으로 묶어
  Noul `entry_{i}` 32 묶음. yes = 확정(`by` = `noul` · 「질문의 말을 이 입구로 읽었다」 표지) / 사이만 = 「입구 없음」
  (되묻기 재료 = 그 후보의 이름 · 코드) / 전부 no = `gap` yes 면 「범위 밖」 · 아니면 「입구 없음」.
- 상품 이름 길(W-3): 상품을 읽는데 상품 시작 표의 입구가 없으면, 대상 조각(`matched.span` · `start` — 허브 `norm` 자리)을
  질문 안 자리로 되짚어 이어 붙여(사이가 공백뿐이면 한 구) 구마다 `offer_name` 입구를 더한다 — 질문 글 그대로 ·
  앱이 낱말을 지어내지 않는다. 이 입구는 속성 시작을 끄지 않는다(R2-F1).
- 단계 ①-1 ~ ①-3(resolve · coverage · Jev 한 요청 · TG · KD · RG)은 `walk.front` 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clinic_chat import prompts
from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.clients.jev_client import Jev
from clinic_chat.config import Settings
from clinic_chat.walk.calls import Entry
from clinic_chat.walk.jev_requests import ask_nouls, base_state
from clinic_chat.walk.ranges import range_name

END_OUT = "범위 밖"
END_NO_ENTRY = "입구 없음"


def _norm_index(question: str) -> list[int]:
    """허브 `norm`(공백 빼고 소문자) 글자 자리 → 질문 글자 자리."""
    return [i for i, ch in enumerate(question) if not ch.isspace()]


def offer_name_spans(question: str, cands: list[dict]) -> list[tuple[int, int]]:
    """resolve 조각 → 질문 글자 범위 [a, b)(EN1 — 상품 이름 구 안에 든 조각인가)."""
    idx = _norm_index(question)
    iv = []
    for c in cands:
        m = c.get("matched") or {}
        span = str(m.get("span") or "")
        if not span:
            continue
        st = m.get("start")
        if isinstance(st, int) and 0 <= st and st + len(span) <= len(idx):
            iv.append((idx[st], idx[st + len(span) - 1] + 1))
            continue
        i = question.find(span)
        if i >= 0:
            iv.append((i, i + len(span)))
    return iv


def offer_name_phrases(question: str, cands: list[dict]) -> list[str]:
    """resolve 조각 → 질문 안에서 이어진(사이가 공백뿐) 조각을 한 구로 — `offer_name_contains` 에 줄 글(W-3)."""
    iv = offer_name_spans(question, cands)
    iv.sort()
    merged: list[list[int]] = []
    for a, b in iv:
        if merged and (a <= merged[-1][1] or not question[merged[-1][1]:a].strip()):
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = []
    for a, b in merged:
        ph = question[a:b].strip()
        if ph and ph not in out:
            out.append(ph)
    return out


@dataclass
class PickResult:
    entries: list[Entry] = field(default_factory=list)
    end: str | None = None
    ask_back: list[dict] = field(default_factory=list)
    trace: dict = field(default_factory=dict)
    build: dict | None = None
    requests: int = 0


class EntryIncomplete(RuntimeError):
    pass


async def pick_entries(question: str, hub: HubRest, jev: Jev, s: Settings, ranges: list[str], gap_yes: bool,
                       build: dict | None) -> PickResult:
    """①-4 — 읽기로 한 범위(들)의 입구 목록 전부(쪽 넘김 끝까지)를 개념으로 묶어 Noul `entry_{i}` 32 묶음."""
    out = PickResult(build=build)
    concepts: dict[str, dict] = {}
    lists = []
    for r in ranges:
        ra = await hub.read_all("resolve", {"range": range_name(r)})
        lists.append(ra.summary())
        if not ra.complete:
            raise EntryIncomplete(f"입구 목록을 끝까지 못 읽음: {ra.error}")
        if out.build is None:
            out.build = ra.build
        for row in ra.items:
            cid = f"{row.get('entity_table')}:{row.get('entity_code')}"
            c = concepts.setdefault(cid, {"id": cid, "table": row.get("entity_table"), "code": row.get("entity_code"),
                                          "name": row.get("entity_name") or "", "names": []})
            if row.get("text") and row["text"] not in c["names"]:
                c["names"].append(row["text"])
    items = [{"id": c["id"], "name": c["name"], "names": c["names"]} for c in concepts.values()]
    state = base_state(question, out.build, ranges=[{"code": r, "name": range_name(r)} for r in ranges])
    vals, _, n_req = await ask_nouls(jev, state, items, "entry", prompts.jev("entry"), s.jev_batch_size)
    out.requests = n_req
    yes, unc, per = [], [], []
    for c, v in zip(concepts.values(), vals, strict=True):
        dec = "confirmed" if v >= s.jev_noul_yes_at else ("no" if v <= s.jev_noul_no_at else "uncertain")
        per.append({"id": c["id"], "noul": v, "decision": dec})
        if dec == "confirmed":
            yes.append(c)
        elif dec == "uncertain":
            unc.append(c)
    result = "confirmed" if yes else ("ask_back" if unc else "none")
    out.trace = {"concepts": len(items), "requests": n_req, "ranges": list(ranges), "lists": lists,
                 "per_candidate": per, "thresholds": {"yes": s.jev_noul_yes_at, "no": s.jev_noul_no_at},
                 "result": result}
    if result == "ask_back":
        out.end = END_NO_ENTRY
        out.ask_back = [{"id": c["id"], "name": c["name"], "names": c["names"]} for c in unc]
    elif result == "none":
        out.end = END_OUT if gap_yes else END_NO_ENTRY
    else:
        out.entries = [Entry(c["table"], c["code"], c["name"], "noul",
                             read_as={"by": "entry_pick", "table": c["table"], "code": c["code"]}) for c in yes]
    return out


__all__ = ["END_NO_ENTRY", "END_OUT", "EntryIncomplete", "PickResult", "offer_name_phrases", "offer_name_spans",
           "pick_entries"]
