"""③ 앱 검증(SPEC-008 §2-3 VF1 ~ VF12 · WORK-017 WP-3) — 검증 뒤 값(`Accepted`)이 그 턴의 유일한 입구다(VF12).

차례: VF1 읽기 → 턴 종류(VF6 셋 밖 · VF7 첫 턴 `follow` · VF11 잇단 `talk`) → VF2 목록에 없음 · 꼴 틀림 → VF3 같은 것 한 번 →
VF4 같은 이름(계열 > 시술 > 제품) 접기(VF5 고민 ↔ 부위는 안 접음) → VF6 `talk` 의 대상 버림 · `follow` + `[]` 앞 대상 →
VF8 ⓐ ⓑ 부위 거르개 받침 → VF9 거둠 → VF10 정보 종류 · bool 칸.
턴 종류를 먼저 정한다 — VF11 이 `talk` 를 `new` 로 읽은 턴은 대상을 버리지 않는다(뒤에서 버리면 되살릴 길이 없다).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from clinic_chat.consult import turn_kind as tk
from clinic_chat.consult.entry_list import TABLES, EntryList
from clinic_chat.walk.kinds import KIND_CODES

KINDS = ("new", "follow", "talk")
WIDE = ("modality", "procedure", "product")  # VF4 — 넓은 것부터
LIVE = ("said", "confirmed", "guess")


class UnderstandJSONError(ValueError):
    pass


@dataclass
class Accepted:
    targets: list[dict] = field(default_factory=list)  # {table, code, name, sure} · 순서 = 출력 순서
    kind: str = "new"
    kind_from: str = "understand"
    sites: list[str] = field(default_factory=list)
    withdraw: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    out: bool = False
    dropped: list[dict] = field(default_factory=list)
    merged: list[dict] = field(default_factory=list)
    added_from_sites: list[str] = field(default_factory=list)
    follow_target: dict | None = None

    def view(self) -> dict:
        """장부 `consult.understand.accepted`(꼴 = `parsed` 와 같은 칸)."""
        return {"targets": self.targets, "kind": self.kind, "sites": self.sites, "withdraw": self.withdraw,
                "kinds": self.kinds, "out": self.out}

    def as_dict(self) -> dict:
        return asdict(self)


def norm(s: str) -> str:
    """VF4 — 허브 R1 과 같은 식(공백 연속 제거 + 소문자 · 다른 정규화 없음)."""
    return re.sub(r"\s+", "", s or "").lower()


def parse(output_raw: str) -> dict:
    """VF1 — 첫 `{` 부터 마지막 `}` 까지."""
    i, j = (output_raw or "").find("{"), (output_raw or "").rfind("}")
    if i < 0 or j < i:
        raise UnderstandJSONError("no_object")
    try:
        d = json.loads(output_raw[i:j + 1])
    except ValueError as e:
        raise UnderstandJSONError("json") from e
    if not isinstance(d, dict):
        raise UnderstandJSONError("not_object")
    return d


def parsed_view(d: dict) -> dict:
    """장부 `consult.understand.parsed` — 읽은 그대로(여섯 칸만)."""
    return {k: d.get(k) for k in ("targets", "kind", "sites", "withdraw", "kinds", "out")}


def _live(obj: dict, table: str, code: str) -> bool:
    from clinic_chat.consult import state as cst

    it = cst.find(obj, table, code)
    return it is not None and it.get("state") in LIVE


def verify(parsed: dict, el: EntryList, obj: dict, *, first_turn: bool, talk_streak: int, talk_max: int,
           prev_row: dict | None) -> Accepted:
    a = Accepted()
    # 턴 종류(VF6 · VF7 · VF11)
    kind = parsed.get("kind")
    if kind not in KINDS:
        kind, a.kind_from = "new", "bad_value"
    if first_turn and kind == "follow":
        kind, a.kind_from = "new", "first_follow"
    if kind == "talk" and talk_streak >= talk_max:
        kind, a.kind_from = "new", "talk_max"
    a.kind = kind
    # VF2 · VF3 · VF10(sure)
    raw = parsed.get("targets")
    seen: dict[tuple[str, str], dict] = {}
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict) or not isinstance(x.get("table"), str) or not isinstance(x.get("code"), str):
            a.dropped.append({"table": (x or {}).get("table") if isinstance(x, dict) else None,
                              "code": (x or {}).get("code") if isinstance(x, dict) else None, "why": "bad_shape"})
            continue
        t, c = x["table"], x["code"]
        if t not in TABLES or not el.has(t, c):
            a.dropped.append({"table": t, "code": c, "why": "not_in_gold"})
            continue
        sure = x.get("sure") if isinstance(x.get("sure"), bool) else False
        if (t, c) in seen:
            if seen[(t, c)]["sure"] != sure:
                seen[(t, c)]["sure"] = False
            a.dropped.append({"table": t, "code": c, "why": "dup"})
            continue
        seen[(t, c)] = {"table": t, "code": c, "name": el.name(t, c), "sure": sure}
    tg = list(seen.values())
    # VF4 — 계열 · 시술 · 제품 같은 이름 → 가장 넓은 것 하나
    groups: dict[str, list[dict]] = {}
    for x in tg:
        if x["table"] in WIDE:
            groups.setdefault(norm(x["name"]), []).append(x)
    drop_ids: set[int] = set()
    for xs in groups.values():
        if len(xs) < 2:
            continue
        keep = min(xs, key=lambda x: WIDE.index(x["table"]))
        folded = [x for x in xs if x is not keep]
        drop_ids |= {id(x) for x in folded}
        a.merged.append({"kept": f"{keep['table']}:{keep['code']}",
                         "folded": [f"{x['table']}:{x['code']}" for x in folded]})
    tg = [x for x in tg if id(x) not in drop_ids]
    # VF6 — talk 의 대상 버림 · follow + [] → 앞 대상
    if kind == "talk" and tg:
        a.dropped += [{"table": x["table"], "code": x["code"], "why": "talk"} for x in tg]
        tg = []
    if kind == "follow" and not tg:
        ft = tk.follow_target(obj, prev_row)
        if ft is not None and el.has(ft["table"], ft["code"]):
            a.follow_target = {"table": ft["table"], "code": ft["code"]}
            tg = [{"table": ft["table"], "code": ft["code"], "name": ft.get("name") or el.name(ft["table"], ft["code"]),
                   "sure": True}]
    # VF8 ⓐ ⓑ — 부위 거르개는 site 코드만 · sites 에만 있는 부위는 targets 끝에
    sites: list[str] = []
    rs = parsed.get("sites")
    for sc in rs if isinstance(rs, list) and kind != "talk" else []:
        if isinstance(sc, str) and el.has("site", sc):
            if sc not in sites:
                sites.append(sc)
        else:
            a.dropped.append({"table": "site", "code": sc if isinstance(sc, str) else None, "why": "not_in_gold",
                              "field": "sites"})
    have = {(x["table"], x["code"]) for x in tg}
    for sc in sites:
        if ("site", sc) not in have:
            tg.append({"table": "site", "code": sc, "name": el.name("site", sc), "sure": True})
            a.added_from_sites.append(sc)
    # VF9 — 거둠: "table:code" · 객체에 살아 있는 것만 · targets 와 겹치면 거둠이 앞선다
    wd: list[str] = []
    rw = parsed.get("withdraw")
    for w in rw if isinstance(rw, list) else []:
        if not isinstance(w, str) or ":" not in w:
            continue
        t, _, c = w.partition(":")
        if _live(obj, t, c) and w not in wd:
            wd.append(w)
    if wd:
        gone = set(wd)
        a.dropped += [{"table": x["table"], "code": x["code"], "why": "withdraw"} for x in tg
                      if f"{x['table']}:{x['code']}" in gone]
        tg = [x for x in tg if f"{x['table']}:{x['code']}" not in gone]
        sites = [s for s in sites if f"site:{s}" not in gone]
    # VF10 — 정보 종류(걷기 `KIND_CODES` 순 · 한 번) · out
    rk = parsed.get("kinds")
    got = {k for k in (rk if isinstance(rk, list) else []) if isinstance(k, str)}
    a.kinds = [k for k in KIND_CODES if k in got]
    a.out = parsed.get("out") if isinstance(parsed.get("out"), bool) else False
    a.targets, a.sites, a.withdraw = tg, sites, wd
    if a.kind == "new" and not tg and wd:  # 검수 L-1 — 대상 없이 거둠만 = talk 갈래(객체가 바뀌면 조회 · 리스트 — TT2 · 사용자 확인)
        a.kind, a.kind_from = "talk", "withdraw_only"
    return a
