"""객체 기준 조회(SPEC-005 §3-3 QY1 ~ QY6 · WORK-012 WP-3 · 검수 W-7 · 코디 W-1 (가) · W-2 · W-9).

- 조건의 꼴 = 「객체에 있는데 아직 안 읽음」 — 읽은 것 = 대화 메모리(`Conv.memo` — `Call.key()` → Read 목록 · B-10).
  서버를 다시 띄워 메모리가 비면 필요할 때 허브를 다시 읽는다(허브는 결정론 · 대화의 `gold_hash` 는 고정).
- **걷기와 겹침**(검수 W-7): 조회는 걷기의 `Call` 을 그대로 지어 `key()` 로 견준다(부위 거르개 값도 걷기와 같은 꼴 — 이름 글자).
  걷기가 이번 턴에 읽은 조회는 다시 안 부르고 그 응답을 쓴다 → `queries[]` 에 `skipped: "walk"`.
- 읽기는 걷기의 함수를 **부르기만** 한다(`walk.breadth.read_calls` — 쪽 넘김 끝까지 + 후보별 profile 이어 조회). 상담 쪽의
  `Verified` · 방문 장부는 따로 세운다(걷기의 것에 섞지 않는다).
- QY1 · QY5: `focus` 고민의 `concern_candidates`(+ 짝 고민이 `focus` 일 때만 그 부위 — W-2) · QY2: 축 대상의 `scope` ·
  QY3: 축 대상만 있고 축 고민이 없을 때 `facets(indication)` · QY7(코디 W-1 (가)): `focus` 가 없고 축 부위만 있을 때 그 부위의
  `facets(indication)` — 선택지(고민)로만 쓴다. `wishes[]` 는 조회를 일으키지 않는다(W-9).
- QY6: 이 모듈이 내는 도구 · facet 은 닫힌 목록(`ALLOWED`) — 안전 종류(adverse · contraindication · avoid) 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clinic_chat.consult import state as st
from clinic_chat.consult.sites import from_response
from clinic_chat.walk.breadth import BreadthError, Read, read_calls
from clinic_chat.walk.breadth import Ledger as VisitLedger
from clinic_chat.walk.calls import Call
from clinic_chat.walk.ranges import ROLES_BASE
from clinic_chat.walk.verified import Verified

ALLOWED = {("concern_candidates", None), ("scope", None), ("facets", "indication"), ("pair", None)}  # QY6
LAYER_ORDER = ("procedure_case", "procedure", "modality", "product")  # BK2 — 자기 → 케이스 → 시술 → 계열
SELF, INHERITED = ROLES_BASE  # 허브 `role` 값(자기 · 물려받음)
SUB_CONCERN = "\ud558\uc704 \uace0\ubbfc"  # 허브 `concern_match` 값 「하위 고민」(SPEC-001 §4-5 — 앱 문장 아님)


@dataclass
class Plan:
    queries: list[dict] = field(default_factory=list)  # 장부 `consult.queries[]`
    lists: list[Read] = field(default_factory=list)  # QY1 · QY5 의 Read(+ profile 이어 조회) — 리스트 ②
    options: dict[str, list[dict]] = field(default_factory=dict)  # 대상 · 부위 노드 → 고민 선택지(QY3 · QY7)
    sub_concerns: list[dict] = field(default_factory=list)  # BK3 — focus 응답 행의 하위 고민
    sub_sites: list[dict] = field(default_factory=list)  # BK4 — 하위 고민들의 고민 부위


def allowed(call: Call) -> bool:
    return (call.tool, call.request.get("facet")) in ALLOWED


class Reader:
    """메모리 → 걷기의 이번 턴 읽기 → 허브 순으로 응답을 얻는다(같은 조회는 한 번)."""

    def __init__(self, hub, memo: dict, walk_reads: list[Read], queries: list[dict]) -> None:
        self.hub, self.memo, self.queries = hub, memo, queries
        self.walk = {}
        for r in walk_reads:
            self.walk.setdefault(r.call.key(), r)
        self.walk_profiles = [r for r in walk_reads if r.call.meta.get("followup") and r.call.meta.get("from_group")]
        self.verified, self.visits, self.reads = Verified(), VisitLedger(), []

    async def get(self, call: Call, rule: str) -> list[Read]:
        assert allowed(call), call.key()  # QY6
        k = call.key()
        if k in self.walk:
            r = self.walk[k]
            got = [r, *(self.walk_profiles if call.tool == "concern_candidates" else [])]
            self.memo[k] = got
            self.queries.append({"rule": rule, "tool": call.tool, "request": call.request, "total": r.ra.total,
                                 "ms": 0.0, "skipped": "walk"})
            return got
        if k in self.memo:
            return self.memo[k]
        before = len(self.reads)
        err = None
        try:
            await read_calls(self.hub, [call], self.verified, self.visits, self.reads, depth="consult",
                             follow=call.tool == "concern_candidates")
        except BreadthError as e:
            err = str(e)[:200]
        got = self.reads[before:]
        head = got[0] if got else None
        self.queries.append({"rule": rule, "tool": call.tool, "request": call.request,
                             "total": head.ra.total if head else None,
                             "ms": round(sum(r.ra.ms for r in got), 1), "skipped": None,
                             **({"error": err} if err else {})})
        if err is None:
            self.memo[k] = got
        return got


def qy1_calls(obj: dict) -> list[Call]:
    """QY1 · QY5 — 걷기의 `_value_calls` 와 같은 꼴(거르개 = 부위 이름 글자 · 부위마다 하나)."""
    f = obj.get("focus")
    if not f:
        return []
    names = list(dict.fromkeys(x["name"] for x in st.filter_sites(obj) if x.get("name")))
    if not names:
        return [Call("concern_candidates", {"concern_code": f}, ("indication",), ("concern",))]
    return [Call("concern_candidates", {"concern_code": f, "site": x}, ("indication",), ("concern",)) for x in names]


def scope_call(n: str) -> Call:
    return Call("scope", {"node": n}, ("lineup",), ("procedure",))


def indication_call(n: str) -> Call:
    return Call("facets", {"node": n, "facet": "indication"}, ("indication",), ("procedure",))


def _rows(reads: list[Read]) -> list[dict]:
    return list(reads[0].ra.items) if reads else []


def concern_options(rows: list[dict]) -> list[dict]:
    """QY3 · QY7 의 행 → 서로 다른 고민(층이 가까운 것부터 — 자기 → 케이스 → 시술 → 계열 · 층 안은 허브 순서)."""
    best: dict[str, dict] = {}
    order: list[str] = []
    for i, r in enumerate(rows):
        code = r.get("concern_code")
        if not code:
            continue
        lay = r.get("layer") or {}
        role = lay.get("role") or SELF
        h = lay.get("holder") or {}
        rank = 0 if role == SELF else 1 + (LAYER_ORDER.index(h.get("holder_table"))
                                             if h.get("holder_table") in LAYER_ORDER else len(LAYER_ORDER))
        o = best.get(code)
        if o is None:
            best[code] = {"table": "concern", "code": code, "name": r.get("concern_name") or "", "layer": role,
                          "holder": h.get("holder_name") or "", "rank": rank, "first": i, "gids": [str(r.get("gid"))]}
            order.append(code)
        else:
            o["gids"].append(str(r.get("gid")))
            if rank < o["rank"]:
                o.update({"layer": role, "holder": h.get("holder_name") or "", "rank": rank})
    out = sorted((best[c] for c in order), key=lambda o: (o["rank"], o["first"]))
    for o in out:
        o["rows"] = len(o["gids"])
    return out


def cc_rows(reads: list[Read]) -> list[dict]:
    out = []
    for r in reads:
        if r.call.tool != "concern_candidates":
            continue
        resp = r.ra.as_response()
        for g in resp.get("groups") or []:
            out.extend(g.get("rows") or [])
        for b in ("modality_only", "unreached"):
            out.extend(resp.get(b) or [])
    return out


def sub_concerns(reads: list[Read], asked: str) -> list[dict]:
    """BK3 — 응답 행의 서로 다른 하위 고민(`concern_match` = 「하위 고민」 · 물은 고민 자신 빼고 · 허브 순서)."""
    out: dict[str, dict] = {}
    for x in cc_rows(reads):
        code = x.get("concern_code")
        if code and code != asked and x.get("concern_match") == SUB_CONCERN and code not in out:
            out[code] = {"table": "concern", "code": code, "name": x.get("concern_name") or "", "layer": SUB_CONCERN}
    return list(out.values())


def concern_sites_of(reads: list[Read]) -> list[dict]:
    pairs: list[dict] = []
    for r in reads:
        if r.call.tool == "concern_candidates":
            pairs.extend(from_response(r.ra.as_response()))
    seen, out = set(), []
    for p in pairs:
        k = (p["concern_code"], p["site_code"])
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


async def run(obj: dict, reader: Reader) -> Plan:
    """L5 — 걷기가 읽지 않은 것만(세 종료 상태 턴에는 부르지 않는다 — 다음 턴이 따라잡는다 · W-6)."""
    out = Plan(queries=reader.queries)
    subjects = st.axis_items(obj, "subjects")
    concerns = st.axis_items(obj, "concerns")
    for s in subjects:  # QY2
        await reader.get(scope_call(st.node(s)), "QY2")
    if subjects and not concerns:  # QY3
        for s in subjects:
            out.options[st.node(s)] = concern_options(_rows(await reader.get(indication_call(st.node(s)), "QY3")))
    f = obj.get("focus")
    if f:
        for c in qy1_calls(obj):  # QY1 · QY5
            out.lists.extend(await reader.get(c, "QY5" if c.request.get("site") else "QY1"))
        out.sub_concerns = sub_concerns(out.lists, f)
        pairs = concern_sites_of(out.lists)
        subs = {x["code"] for x in out.sub_concerns}
        seen: dict[str, dict] = {}
        for p in pairs:
            if p["concern_code"] in subs and p["site_code"] not in seen:
                seen[p["site_code"]] = {"table": "site", "code": p["site_code"], "name": p["site_name"], "layer": ""}
        out.sub_sites = list(seen.values())
    else:
        for s in st.axis_items(obj, "sites"):  # QY7(코디 W-1 (가)) — 부위만 대상 → 그 부위의 고민을 선택지로
            if (s.get("source") or {}).get("pair_concern"):
                continue
            out.options[st.node(s)] = concern_options(_rows(await reader.get(indication_call(st.node(s)), "QY7")))
    derive(obj, reader.memo, out)
    return out


def derive(obj: dict, memo: dict, plan: Plan | None = None) -> None:
    """UP8 — 파생 칸(`procedures[]` · `concern_sites[]`)을 다시 센다. 근거 항목이 거둬지면 빠진다 · 계열은 시술을 정하지 않는다."""
    procs: list[dict] = []
    seen: set[str] = set()
    for s in st.axis_items(obj, "subjects"):
        if s["table"] == "modality":
            continue
        reads = memo.get(scope_call(st.node(s)).key()) or []
        holders = _rows(reads)
        picks = []
        if s["table"] == "procedure":
            g = next((h for h in holders if h.get("role") == SELF), {})
            picks.append((s["code"], s.get("name") or "", g.get("gid")))
        for h in holders:
            if h.get("holder_table") == "procedure" and h.get("role") == INHERITED:
                picks.append((h.get("holder_code"), h.get("holder_name") or "", h.get("gid")))
        for code, name, gid in picks:
            if code and code not in seen:
                seen.add(code)
                procs.append({"table": "procedure", "code": code, "name": name, "state": s["state"],
                              "source": {"how": "derived"}, "via": {"subject": st.node(s), "scope_gid": gid}})
    obj["procedures"] = procs
    cs: list[dict] = []
    f = obj.get("focus")
    if f:
        reads = plan.lists if plan is not None else [r for c in qy1_calls(obj) for r in memo.get(c.key()) or []]
        for p in concern_sites_of(reads):
            cs.append({"table": "site", "code": p["site_code"], "name": p["site_name"], "concern_code": p["concern_code"],
                       "csi_id": p["csi_id"], "state": "derived", "source": {"how": "derived"}})
    obj["concern_sites"] = cs
