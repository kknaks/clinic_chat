"""단계 ① 맨 앞 판단 — 「무엇에 대한 질문인가(대상) · 어떤 정보가 필요한가(정보 종류)」(SPEC-002 §3-1 ①-1 ~ ①-5).

①-1 resolve(질문 그대로 — CD1) → 개념 하나 = 후보 하나(빼지 않는다 — CD2) → coverage(후보 전부 — CD3)
①-2 Jev **한 요청**: `target_{i}` · `kind_{k}`(14) · `gap` — 고르기 타입 없음(후보 0 이면 kind · gap 만 — CD4)
①-3 앱(결정론): KD1 · KD2 → TG(겹침은 범위 규칙 먼저 · 덮이지 않은 일치는 Noul 과 무관하게 대상 · 사이 값은 둠(TG2) ·
     덮인 조각은 Noul yes 일 때만 대상 — 사이 값 · no 는 `dropped`(_RESUME §2 11-17 「덮인 조각 규칙」))
     → KD5 → KD3 · KD6 → RG1(범위 = 읽기로 한 (대상의 표, 종류) 짝의 범위 합집합) · RG4 · RG5(`scope_gap`)
①-4 후보가 0 일 때만 입구 고르기(TG4 — `walk.entry.pick_entries`)
①-5 조회 계획은 `calls.plan_calls(대상, 읽는 종류)` — Jev 가 못 바꾼다

Jev 응답에서 읽는 것은 `noul` 뿐이다. 후보 · 종류 · 이름은 전부 허브 · 앱이 gold 에서 낸 것이다.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from clinic_chat import prompts
from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.clients.jev_client import Jev
from clinic_chat.config import Settings
from clinic_chat.walk.calls import OFFER_START, Entry
from clinic_chat.walk.entry import END_NO_ENTRY, END_OUT, offer_name_phrases, offer_name_spans, pick_entries
from clinic_chat.walk.groups import group_targets, scope_links
from clinic_chat.walk.jev_requests import ask_front, base_state
from clinic_chat.walk.kinds import (
    ATTR4,
    ATTR_START,
    DEFAULTS,
    ENTRY_KIND,
    HOME_RANGE,
    KIND_CODES,
    KIND_NAME,
    KINDS,
    RANGE_OF,
    TARGET4,
    kinds_for_table,
    range_of,
)
from clinic_chat.walk.ranges import OFFER_NAME, is_attr_start, range_name, start_tables

READ_BY = ("yes", "top", "entry", "kd9", "kd10", "default")
REQUIRED_BY = ("yes", "top", "entry")  # 기본 묶음 · KD9 · KD10 으로만 더한 종류는 required 가 아니다(§3-2 · §3-7-2)
STRONG_BY = ("yes", "top", "entry", "kd9", "kd10")  # KD11 — 그 범위에 「강하게 선」 종류
KD9_SET = ("mechanism", "indication", "outcome", "lineup")  # KD9 — 개요를 물으면 그 대상의 한 벌


@dataclass
class FrontResult:
    entries: list[Entry] = field(default_factory=list)  # 조회 입구(대상 · 값 · 속성 입구 · 상품 이름 길)
    kinds: list[str] = field(default_factory=list)  # 읽는 종류(표 순서)
    required: list[str] = field(default_factory=list)
    ranges: list[str] = field(default_factory=list)
    attr_start: bool = False
    scope_gap: bool = False
    end: str | None = None  # 범위 밖 | 입구 없음(단계 ① 에서 끝남)
    ask_back: list[dict] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)  # node → kind → {self, lower, lower_holders, inherited}
    offered: list[dict] = field(default_factory=list)  # 「더 볼 수 있는 정보」 — {code, name, rows}
    trace_front: dict = field(default_factory=dict)
    trace_entry: dict = field(default_factory=dict)
    trace_entry_pick: dict | None = None
    build: dict | None = None
    jev_requests: int = 0
    jev_models: set[str] = field(default_factory=set)
    by: dict[str, str] = field(default_factory=dict)  # 종류 → 읽은 까닭(KD1 ~ KD11)
    via: dict[str, str] = field(default_factory=dict)
    kinds_by: str = "jev"
    applicable_kinds: dict[str, bool] = field(default_factory=dict)
    groups: list[list[str]] = field(default_factory=list)  # PR1 무리(대상 노드) — 대상 둘 이상일 때
    links: dict[str, dict[str, str]] = field(default_factory=dict)  # 대상 → {scope 의 holder 노드: role}(PR1 (ii) · SD1)
    group_visited: list[dict] = field(default_factory=list)  # 무리 판정 `scope` — 방문 장부 `visited` 에만(W-4)
    group_by: dict[int, str | None] = field(default_factory=dict)

    def read_as_by_node(self) -> dict[str, dict]:
        return {e.node: e.read_as for e in self.entries if e.read_as}

    def applicable(self, node: str, kind: str) -> bool:
        table = node.split(":", 1)[0]
        return range_of(kind, table, self.attr_start) is not None


def _decide_kinds(out: FrontResult, s: Settings, kv: dict, by: dict, entries: list[Entry]) -> None:
    """①-3 의 종류 결정 — KD5 → KD10 → KD9 → KD6 → RG1 · KD3(KD11). `by` = KD1 · KD2 결과(바꾸지 않고 복사해 쓴다)."""
    attr = is_attr_start(e.table for e in entries)
    out.attr_start = attr
    via: dict[str, str] = {}
    # KD5 — 입구가 이름 댄 정보(점수와 무관 · TG2 개념이면 via = TG2)
    for e in entries:
        k = ENTRY_KIND.get(e.table)
        if k and k not in by and range_of(k, e.table, attr):
            by[k] = "entry"
            if e.via == "TG2":
                via[k] = "TG2"
    # KD10 — 사이 값 가운데 `jev_kind_read_at` 이상(설정값 — 코드에 문턱을 박지 않는다)
    for k in KIND_CODES:
        if k not in by and s.jev_noul_no_at < kv[k] < s.jev_noul_yes_at and kv[k] >= s.jev_kind_read_at:
            by[k] = "kd10"
    # KD9 — 개요가 yes · 1위면 그 대상의 한 벌(이미 더 강한 까닭이 있으면 그대로 · 표에 없는 종류는 KD4 로 skipped)
    if by.get("overview") in ("yes", "top"):
        for k in KD9_SET:
            by.setdefault(k, "kd9")
    kinds_by = "jev"
    applicable = {k: any(range_of(k, e.table, attr) for e in entries) for k in KIND_CODES}
    # KD6 — 그 대상 표의 기본 묶음만. 읽기로 한 종류가 대상의 표에 전부 없을 때(KD4 로 다 건너뜀 — 읽을 종류 0)도
    # 같다(코드 검수 v05 W-1 — 빈손 · 「실행 중단」 대신). 건너뛴 종류는 `skipped` 로 그대로 남는다.
    if not any(applicable[k] for k in by):
        kinds_by = "default"
        for e in entries:
            home = HOME_RANGE.get(e.table)
            ks = [k for k in DEFAULTS.get(home or "", ()) if range_of(k, e.table, attr)] or list(kinds_for_table(e.table, attr))
            for k in ks:
                by.setdefault(k, "default")
    # RG1 — 범위 = 읽기로 한 (대상의 표, 종류) 짝의 범위 합집합 → KD3 기본 묶음과 합집합.
    # KD11 — 그 범위에 강하게 선 종류가 있으면 noul 이 no 문턱 이하인 기본 묶음 종류는 읽지 않고 `offered`(KD6 가지는 그대로)
    ranges = _ranges_for((e.table, k, attr) for e in entries for k in KIND_CODES if k in by)
    strong_ranges = set(_ranges_for((e.table, k, attr) for e in entries for k in KIND_CODES
                                    if by.get(k) in STRONG_BY))
    kd11: list[str] = []
    for r in list(ranges):
        for k in DEFAULTS.get(r, ()):
            if k in by:
                continue
            if kinds_by != "default" and r in strong_ranges and kv[k] <= s.jev_noul_no_at:
                if k not in kd11:
                    kd11.append(k)
                continue
            by[k] = "default"
    out.ranges = ranges
    out.kinds = [k for k in KIND_CODES if k in by and applicable[k]]
    out.required = [k for k in out.kinds if by[k] in REQUIRED_BY]
    nodes = [e.node for e in entries]
    out.offered = [{"code": k, "name": KIND_NAME[k], "rows": _kind_rows(out.coverage, nodes, k),
                    **({"by": "kd11"} if k in kd11 else {})}
                   for k in KIND_CODES if k not in by and applicable[k]
                   and (_decide(kv[k], s) == "between" or k in kd11)]
    out.by, out.via, out.kinds_by, out.applicable_kinds = by, via, kinds_by, applicable


async def _offer_name_cover(question: str, hub: HubRest, phrases: list[str], concepts: list[dict]) -> dict[str, str]:
    """EN1 — 상품 이름 길이 **맞았을 때만**(0 행이면 덮지 않는다 · B-8) 그 구의 글자 범위 안에 든 후보 → {후보 노드: 구}."""
    out: dict[str, str] = {}
    for ph in phrases:
        i = question.find(ph)
        if i < 0:
            continue
        r = await hub.call("facets", {"facet": "offer", "offer_name_contains": ph, "limit": 1})
        if not r.get("total"):
            continue
        for c in concepts:
            iv = offer_name_spans(question, [c["raw"]])
            if iv and all(i <= a and b <= i + len(ph) for a, b in iv):
                out[c["id"]] = ph
    return out


def _decide(v: float, s: Settings) -> str:
    return "yes" if v >= s.jev_noul_yes_at else ("no" if v <= s.jev_noul_no_at else "between")


async def _coverage(hub: HubRest, nodes: list[str]) -> tuple[dict, dict | None]:
    if not nodes:
        return {}, None
    r = await hub.coverage(nodes)
    out: dict[str, dict] = {}
    for it in r.get("items") or []:
        out[str(it.get("node"))] = {str(k.get("kind")): {x: k.get(x) for x in ("self", "lower", "lower_holders", "inherited")}
                                    for k in it.get("kinds") or []}
    return out, r.get("build")


def nonzero_rows(cov: dict) -> dict:
    """후보 설명의 `rows` — 종류별 {self, lower, inherited} 가운데 0 이 아닌 것(§3-3)."""
    out = {}
    for k, c in (cov or {}).items():
        d = {x: c.get(x) for x in ("self", "lower", "inherited") if c.get(x)}
        if d:
            out[k] = d
    return out


def _kind_rows(cov: dict, nodes: list[str], kind: str) -> int:
    return sum(sum((cov.get(n, {}).get(kind) or {}).get(x) or 0 for x in ("self", "lower", "inherited")) for n in nodes)


def _ranges_for(pairs) -> list[str]:
    out: list[str] = []
    for t, k, attr in pairs:
        r = range_of(k, t, attr)
        if r and r not in out:
            out.append(r)
    return out


async def front(question: str, hub: HubRest, jev: Jev, s: Settings, timer=None) -> FrontResult:
    out = FrontResult()

    def stage(name: str):
        return timer.stage(name) if timer is not None else contextlib.nullcontext()

    # ①-1 resolve(맞춤 꼴 — 쪽 넘김 없음) · 개념 하나 = 후보 하나
    with stage("resolve"):
        res = await hub.resolve(question)
    out.build = res.get("build")
    concepts: list[dict] = []
    seen: set[str] = set()
    for c in res.get("candidates") or []:
        cid = f"{c.get('entity_table')}:{c.get('entity_code')}"
        if cid in seen:
            continue
        seen.add(cid)
        m = c.get("matched") or {}
        concepts.append({"id": cid, "table": c.get("entity_table"), "code": c.get("entity_code"),
                         "name": c.get("entity_name") or c.get("text") or "", "span": m.get("span"),
                         "start": m.get("start"), "covered_by": m.get("covered_by"), "raw": c})
    with stage("coverage"):
        out.coverage, cb = await _coverage(hub, [c["id"] for c in concepts])
    out.build = out.build or cb

    # ①-2 Jev 한 요청
    instr = prompts.jev_sections("front")
    catalog = [{"code": k.code, "name": k.name} for k in KINDS]
    cand_state = [{"id": c["id"], "name": c["name"], "table": c["table"], "matched": c["span"],
                   "rows": nonzero_rows(out.coverage.get(c["id"], {}))} for c in concepts]
    with stage("jev"):
        t_noul, k_noul, gap, n_req, models = await ask_front(jev, base_state(question, out.build), cand_state,
                                                             catalog, instr, s.jev_batch_size)
    out.jev_requests += n_req
    out.jev_models |= models
    kv = dict(zip(KIND_CODES, k_noul, strict=True))
    out.scope_gap = gap >= s.jev_noul_yes_at  # RG5 — 사이는 기록만
    gap_yes = out.scope_gap

    # ①-3 KD1 · KD2 — yes 종류 + 가장 높은 종류(사이여도 — 전부 no 이하면 KD6)
    by: dict[str, str] = {k: "yes" for k in KIND_CODES if kv[k] >= s.jev_noul_yes_at}
    top = max(KIND_CODES, key=lambda k: (kv[k], -KIND_CODES.index(k)))
    if top not in by and kv[top] > s.jev_noul_no_at:
        by[top] = "top"
    targets_tr: list[dict] = []
    overlaps: list[dict] = []
    dropped: list[dict] = []
    entries: list[Entry] = []
    entry_by = "rule"

    if not concepts:  # CD4 · TG4 — ①-4 입구 고르기
        if by:
            rs = [r for k in by for r in RANGE_OF[k].values() if r != ATTR_START]
        elif gap_yes:
            rs = []
        else:
            rs = [r for r in RANGE_OF[top].values() if r != ATTR_START]
        rs = list(dict.fromkeys(rs))
        if not rs:  # 종류 전부 no 이하 · gap yes — 읽을 범위가 없다
            out.end = END_OUT
        else:
            pk = await pick_entries(question, hub, jev, s, rs, gap_yes, out.build)
            out.jev_requests += pk.requests
            out.trace_entry_pick = pk.trace
            out.build = out.build or pk.build
            if pk.end:
                out.end = pk.end
                out.ask_back = pk.ask_back
            else:
                entries = pk.entries
                entry_by = "noul"  # SPEC §3-1 입구 고르기 「지킬 것」 — 확정한 입구는 `entry.by` = `noul`
                extra_cov, _ = await _coverage(hub, [e.node for e in entries])
                out.coverage.update(extra_cov)
        if out.end:
            if out.end == END_OUT:
                out.scope_gap = False
            out.trace_front = _front_trace(out, s, kv, by, gap, targets_tr, dropped, [], {}, "jev")
            out.trace_entry = {"resolve_candidates": [], "picked": [], "by": "noul", "overlaps": [], "unused": []}
            return out
    else:
        tv = dict(zip([c["id"] for c in concepts], t_noul, strict=True))
        # TG3 — 같은 조각의 겹침은 범위 규칙 먼저(N-5: 겹친 후보마다 (그 후보의 표, yes · 1위 종류) 짝으로 범위)
        by_span: dict[str, list[dict]] = {}
        for c in concepts:
            by_span.setdefault(str(c["span"]), []).append(c)
        lost: set[str] = set()
        for span, group in by_span.items():
            if len(group) < 2:
                continue
            rs = _ranges_for((c["table"], k, True) for c in group for k in by if by[k] in ("yes", "top"))
            if not rs:
                continue
            st = start_tables(rs) | ({c["table"] for c in group if c["table"] in ATTR4} if ATTR_START in rs else set())
            main = [c for c in group if c["table"] in st and c["table"] not in ATTR4]
            attrs = [c for c in group if c["table"] in ATTR4]
            pool = main or attrs
            if len(pool) == 1:
                keep = pool[0]
                for c in group:
                    if c is not keep:
                        lost.add(c["id"])
                overlaps.append({"span": span, "concepts": [c["id"] for c in group], "by": "rule",
                                 "picked": [keep["id"]], "ranges": rs})
        for c in concepts:
            v = tv[c["id"]]
            d = _decide(v, s)
            if c["id"] in lost:
                dec = "overlap"
            elif d == "yes":
                dec = "target"
            elif c["covered_by"] is not None:
                dec = "dropped"  # 덮인 조각은 yes 일 때만 대상 — 사이 값 · no 는 뺀다(_RESUME §2 11-17 「덮인 조각 규칙」)
            elif d == "between":
                dec = "uncertain_kept"  # TG2 — 넘김 쪽(덮이지 않은 조각만)
            else:
                dec = "named"  # TG3 — 이름이 글에 통째로 들었다(덮이지 않은 일치 · no 이하)
            targets_tr.append({"id": c["id"], "name": c["name"], "noul": v, "decision": dec,
                               "covered_by": c["covered_by"]})
            if dec == "dropped":
                dropped.append({"id": c["id"], "noul": v, "covered_by": c["covered_by"]})
            if dec in ("target", "uncertain_kept", "named"):
                ra = {"uncertain_kept": {"by": "TG2", "table": c["table"], "code": c["code"]},
                      "named": {"by": "TG3", "table": c["table"], "code": c["code"]}}.get(dec)
                entries.append(Entry(c["table"], c["code"], c["name"], dec, read_as=ra,
                                     via="TG2" if dec == "uncertain_kept" else None,
                                     span=c["span"], covered_by=c["covered_by"], start=c["start"],
                                     norm=(c["raw"] or {}).get("norm")))
        entry_by = "noul"

    if not entries:  # 후보는 있었는데 남은 대상 0 — 가장 긴 일치는 덮이지 않으므로 생기지 않는 꼴(지어내지 않는다)
        out.end = END_NO_ENTRY
        out.trace_front = _front_trace(out, s, kv, by, gap, targets_tr, dropped, [], {}, "jev")
        out.trace_entry = _entry_trace(concepts, entries, entry_by, overlaps)
        return out

    _decide_kinds(out, s, kv, dict(by), entries)
    by = out.by

    # 상품 이름 길(W-3 · BR3) — 상품을 읽는데 상품 시작 표의 입구가 없으면
    if "offer" in out.kinds and not any(e.table in OFFER_START for e in entries):
        raw = [c["raw"] for c in concepts if any(c["id"] == e.node for e in entries)]
        phrases = offer_name_phrases(question, raw)
        for ph in phrases:
            entries.append(Entry(OFFER_NAME, "", ph, "rule"))
        # EN1 — 맞은 상품 이름(그 길이 0 행이 아님)의 글자 범위 안에 든 resolve 후보는 덮인 조각: yes 만 대상(B-8)
        with stage("hub"):
            cov = await _offer_name_cover(question, hub, phrases, concepts)
        for e in list(entries):
            ph = cov.get(e.node)
            if ph is None or e.table == OFFER_NAME or e.by == "target":
                continue
            entries.remove(e)
            e.covered_by = ph
            for t in targets_tr:
                if t["id"] == e.node:
                    t["decision"], t["covered_by"], t["covered_by_offer_name"] = "dropped", ph, True
                    dropped.append({"id": e.node, "noul": t["noul"], "covered_by": ph, "by": "offer_name"})
    # PR1 — 대상 둘 이상이면 무리(같은 조각 · scope 의 물려받음 / 하위 *) — 짝 조회의 끝 · SD1 의 층 판정 재료
    targets = [e for e in entries if e.table in TARGET4]
    if len(targets) >= 2:
        with stage("hub"):
            out.links, out.group_visited = await scope_links(hub, [t.node for t in targets])
        gs, bys = group_targets(targets, out.links)
        out.groups = [[t.node for t in g] for g in gs]
        out.group_by = dict(enumerate(bys))
    out.entries = entries
    out.trace_front = _front_trace(out, s, kv, by, gap, targets_tr, dropped, out.kinds, out.via, out.kinds_by,
                                   applicable=out.applicable_kinds)
    out.trace_front["groups"] = [{"members": g, "by": out.group_by.get(i)} for i, g in enumerate(out.groups)] \
        if out.groups else []
    out.trace_entry = _entry_trace(concepts, entries, entry_by, overlaps)
    return out


def _front_trace(out: FrontResult, s: Settings, kv: dict, by: dict, gap: float, targets: list, dropped: list,
                 read: list, via: dict, kinds_by: str, applicable: dict | None = None) -> dict:
    kinds = []
    for k in KIND_CODES:
        v = kv.get(k)
        if k in read:
            dec = "read"
        elif k in by and applicable is not None and not applicable.get(k):
            dec = "skipped"  # 읽기로 했지만 대상의 표에 그 종류의 조회가 없다(KD4)
        elif any(o["code"] == k for o in out.offered):
            dec = "offered"  # 사이 값(KD3 · KD10 아래) · KD11 로 남긴 no 이하 기본 묶음
        elif v is not None and s.jev_noul_no_at < v < s.jev_noul_yes_at:
            dec = "no"
        else:
            dec = "no"
        d = {"code": k, "noul": v, "decision": dec, "by": by.get(k) if dec in ("read", "skipped") else None}
        if k in via:
            d["via"] = via[k]
        kinds.append(d)
    return {"coverage": out.coverage, "targets": targets, "dropped": dropped, "kinds": kinds, "kinds_by": kinds_by,
            "gap": gap, "scope_gap": out.scope_gap, "ranges": [{"code": r, "name": range_name(r)} for r in out.ranges],
            "attr_start": out.attr_start, "required": [], "offered": out.offered,
            "thresholds": {"yes": s.jev_noul_yes_at, "no": s.jev_noul_no_at, "kind_read": s.jev_kind_read_at},
            "jev_requests": out.jev_requests}


def _entry_trace(concepts: list[dict], entries: list[Entry], by: str, overlaps: list) -> dict:
    return {"resolve_candidates": [{"id": c["id"], "span": c["span"], "start": c["start"], "covered_by": c["covered_by"]}
                                   for c in concepts],
            "picked": [{"id": e.node, "by": e.by, "via": e.via} for e in entries if e.table != OFFER_NAME],
            "offer_name": [e.name for e in entries if e.table == OFFER_NAME],
            "by": by, "overlaps": overlaps, "unused": []}
