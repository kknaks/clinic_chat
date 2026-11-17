"""B 열 — gold 그래프 + Jev(단계 ① ~ ⑤ · SPEC-002 §3 · §4 · §5 · §6-1 · 0.5.1).

① 맨 앞 판단(`walk.front`) → ② 고른 대상 × 읽는 종류만 끝까지(`calls.plan_calls` · 하위 층 자기 행 포함) → (N-1 표지용 조회)
→ ③ 깊이(`walk.depth` — 시술 범위는 없음) → ④ 재료(`walk.material` — 싣는 칸 · 줄 번호 · 표지) + 틀린 말 검사(`walk.gate`)
→ (가) 판정용 조회(읽은 조회가 전부 0 · `ledger_check[]` 가 있을 때만) → 종료 상태 → ⑤ 답(도구 없음 · 가장 낮은 effort).

- 「범위 밖」 · 「입구 없음」 · 「실행 중단」 에서도 단계 ⑤ 를 부른다 — 입력 = `end_state` + 사유 코드 + **빈 재료**. `b_on` 없음.
- 답 꼬리(O-25 ④): 답 본문 뒤 `TAIL_MARK` 줄 뒤에 「답 줄: 재료 줄 번호」 — 앱이 떼어 `answer_lines[]` 로 남기고 `answer` 에는
  꼬리가 없다. 꼬리가 없거나 깨졌으면 답은 그대로 · `answer_lines` = `null`. 스트리밍 화면에는 표식 뒤를 흘리지 않는다.
- `read_kinds` 를 주면 읽는 종류를 덮어쓴다(「B 전체」 `b_full` — 단계 ① 은 같고 종류만 그 대상의 표에 있는 것 전부 · B-2).
  앱 코드는 사용자에게 보일 문장을 갖지 않는다(문구는 `prompts/` 에만).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from clinic_chat import progress
from clinic_chat.clients.hub_rest import HubError
from clinic_chat.clients.jev_client import JevError
from clinic_chat.columns.base import ColumnOut, Deps, cited_gids, column
from clinic_chat.llm import cli
from clinic_chat.timing import Timer
from clinic_chat.walk import end_state as es
from clinic_chat.walk import value_pick
from clinic_chat.walk.breadth import BreadthError, Ledger, Read, read_calls
from clinic_chat.walk.calls import Call, plan_calls, range_kinds
from clinic_chat.walk.depth import DepthResult, StepLimit, walk_depth
from clinic_chat.walk.entry import EntryIncomplete
from clinic_chat.walk.fold import FoldTooLarge, est_tokens, pages
from clinic_chat.walk.front import FrontResult, front
from clinic_chat.walk.gate import check
from clinic_chat.walk.kinds import KIND_CODES, KIND_NAME, TARGET4, range_of
from clinic_chat.walk.material import Material, answer_check, build, roster, roster_text, to_lines
from clinic_chat.walk.ranges import ROLES_BASE, SAME_DAY_PACKAGE_KINDS, range_name
from clinic_chat.walk.verified import NO_PROCEDURE_NAME, Verified, case_node, response_rows

NO_B_ON = (es.OUT, es.NO_ENTRY, es.ABORTED)
TAIL_MARK = "<<<근거>>>"
TAIL_LINE = re.compile(r"^\s*(\d+)\s*[:：]\s*((?:\d+(?:\s*-\s*\d+)?)(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*)\s*$")


@dataclass
class Walk:
    front: FrontResult | None = None
    verified: Verified = field(default_factory=Verified)
    reads: list[Read] = field(default_factory=list)
    ledger: Ledger = field(default_factory=Ledger)
    planned: list[Call] = field(default_factory=list)
    depth: DepthResult | None = None
    gate: dict | None = None
    material: Material = field(default_factory=Material)
    end_state: str | None = None
    reason: str | None = None
    error: dict | None = None
    end_check: dict | None = None
    labels: dict[str, str] = field(default_factory=dict)
    timer: Timer = field(default_factory=Timer)
    value_pick: dict | None = None

    @property
    def groups(self) -> list[dict]:
        return self.material.groups

    def build(self) -> dict | None:
        if self.front and self.front.build:
            return self.front.build
        return next((r.ra.build for r in self.reads if r.ra.build), None)


async def _labels(d: Deps, w: Walk) -> None:
    """N-1 — `procedure_name` 칸이 없는 표의 케이스 행: 그 케이스의 `g_profile.procedure_name` 값(이미 읽은 profile 이면 그것)."""
    need = {cn for v in w.verified.rows.values() if v.gold_table in NO_PROCEDURE_NAME
            for cn in [case_node(v.row)] if cn}
    have = {f"{v.row.get('subject_table')}:{v.row.get('subject_code')}": v.row.get("procedure_name")
            for v in w.verified.rows.values() if v.gold_table == "g_profile" and v.row.get("procedure_name")}
    # HG2 — 거친 층(`layer.via[]`) 이름: 같은 응답의 depth 1 행에서 못 얻은 것만(A-3 · 되적기 후보 — 건수는 trace `label_for`)
    via_need = {n for v in w.verified.rows.values() for n in v.marks.get("layer_via_unnamed") or []}
    via_need -= {n for v in w.verified.rows.values() for n in (v.marks.get("layer_via_names") or {})}
    have.update({f"{v.row.get('subject_table')}:{v.row.get('subject_code')}": v.row.get("name_ko")
                 for v in w.verified.rows.values() if v.gold_table == "g_profile" and v.row.get("name_ko")
                 and f"{v.row.get('subject_table')}:{v.row.get('subject_code')}" in via_need})
    calls = []
    for cn in sorted(need | via_need):
        if have.get(cn):
            w.labels[cn] = have[cn]
        else:
            c = Call("facets", {"node": cn, "facet": "profile", "roles": [ROLES_BASE[0]]}, (), (), "label")
            if cn in via_need and cn not in need:
                c.meta["label_for"] = "layer_via"
            calls.append(c)
    if calls:
        new = await read_calls(d.hub, calls, w.verified, w.ledger, w.reads, follow=False)
        for r in new:
            for row in response_rows(r.ra.as_response()):
                node = f"{row.get('subject_table')}:{row.get('subject_code')}"
                if node in need and row.get("procedure_name"):
                    w.labels.setdefault(node, row["procedure_name"])
                elif node in via_need and (row.get("name_ko") or row.get("procedure_name")):
                    w.labels.setdefault(node, row.get("name_ko") or row["procedure_name"])


def offer_picks(w: Walk) -> None:
    """EN1 — 상품 이름 길(`offer_name_contains`)로 맞은 상품을 `entry.picked[]` 에 `by` = `offer_name` 으로(읽은 뒤에 채운다)."""
    f = w.front
    ids: list[str] = []
    for r in w.reads:
        if r.call.request.get("offer_name_contains") and r.call.purpose == "read":
            for row in response_rows(r.ra.as_response()):
                oid = row.get("offer_id")
                if oid and f"offer:{oid}" not in ids:
                    ids.append(f"offer:{oid}")
    picked = f.trace_entry.setdefault("picked", [])
    for i in ids:
        picked.append({"id": i, "by": "offer_name", "via": None})


def same_day_marks(w: Walk) -> None:
    """SD1 — `same_day` 재료의 「같은 날 조합」 패키지가 두 대상(또는 그 윗층 · 하위)을 항목으로 가지면 그 패키지 행에 표지
    「같은 날 근거(패키지)」 · 항목이 윗층 · 하위면 층 표지(그 대상에 대한 `scope` role — 무리 재료 `front.links`)를 함께."""
    f = w.front
    if not f or "same_day" not in f.kinds:
        return
    targets = [e.node for e in f.entries if e.table in TARGET4]
    if len(targets) < 2:
        return
    rel = {t: {t: None, **(f.links.get(t) or {})} for t in targets}
    pk: dict[str, list] = {}
    for v in w.verified.rows.values():
        if v.gold_table == "g_package" and v.row.get("package_kind") in SAME_DAY_PACKAGE_KINDS:
            pk.setdefault(str(v.row.get("package_id")), []).append(v)
    names = {e.node: e.name for e in f.entries}
    for vs in pk.values():
        items = {f"{v.row.get('item_table')}:{v.row.get('item_code')}" for v in vs}
        if not all(items & set(rel[t]) for t in targets):
            continue
        for v in vs:
            it = f"{v.row.get('item_table')}:{v.row.get('item_code')}"
            hit = [(t, rel[t][it]) for t in targets if it in rel[t]]
            if not hit:
                continue
            v.marks["same_day_package"] = True
            lay = [{"of": names.get(t, ""), "layer": role} for t, role in hit if role]
            if lay:
                v.marks["same_day_layer"] = lay


async def _coverage_of(d: Deps):
    async def f(nodes: list[str]) -> dict:
        try:
            r = await d.hub.coverage(nodes)
        except HubError:  # 깊이 후보 설명의 셈은 보조 재료 — 못 얻으면 셈 없이 묻는다(판정 · 폭에 닿지 않음)
            return {}
        return {str(it.get("node")): {str(k.get("kind")): k for k in it.get("kinds") or []} for it in r.get("items") or []}
    return f


async def walk(question: str, d: Deps, *, read_kinds: list[str] | None = None, carry: list[dict] | None = None,
               instr: dict[str, str] | None = None) -> Walk:
    s = d.settings
    w = Walk()
    t = w.timer
    aborted = None
    try:
        progress.emit("step", step="①", stage="front")
        w.front = await front(question, d.hub, d.jev, s, timer=t, carry=carry, instr=instr)  # 상담만 carry · instr
        f = w.front
        if read_kinds is not None and not f.end:  # b_full — 그 대상의 표에 있는 종류 전부
            f.kinds = [k for k in KIND_CODES if k in read_kinds
                       and any(range_of(k, e.table, f.attr_start) for e in f.entries)]
        if not f.end:
            w.ledger.visited.extend(f.group_visited)  # PR1 무리 판정 scope — visited 에만(lineup 읽기를 가리지 않는다 · W-4)
            plan = plan_calls(f.entries, f.kinds, groups=f.groups or None)
            vpk = value_pick.needed(f)
            defer = value_pick.deferred(plan, vpk)  # W-7 — 값 고르기가 도는 사건 상세는 VP 뒤로
            w.planned = [c for c in plan if c not in defer]
            progress.emit("step", step="②", stage="hub")

            async def rd(calls: list[Call]) -> list[Read]:
                return await read_calls(d.hub, calls, w.verified, w.ledger, w.reads,
                                        read_as_by_node=f.read_as_by_node())
            with t.stage("hub"):
                await rd(w.planned)
            offer_picks(w)  # EN1 — 상품 이름 길로 맞은 상품 = 입구
            if vpk:
                with t.stage("jev"):
                    f.trace_entry["value_pick"] = w.value_pick = await value_pick.run(question, d, w, vpk, defer, rd)
            with t.stage("hub"):
                await _labels(d, w)
            same_day_marks(w)  # SD1
            progress.emit("step", step="③", stage="depth")
            with t.stage("depth"):
                w.depth = await walk_depth(question, d.hub, d.jev, s, f=f, verified=w.verified, ledger=w.ledger,
                                           reads=w.reads, build=w.build(), coverage_of=await _coverage_of(d))
            f.trace_front["required"] = w.depth.required.get("items", [])
            progress.emit("step", step="④", stage="gate")
            with t.stage("gate"):
                inh = {i["kind"] for i in w.depth.required.get("items", []) if i.get("met_by") == "inherited"}
                w.material = build(w.verified, decisions=w.depth.decisions, speak_names=w.labels, inherited_kinds=inh)
                w.material.roster = roster(w.reads, w.verified, {e.node for e in f.entries})  # FT8 · CP2
                w.gate = check(reads=w.reads, verified=w.verified, entries=f.entries, decisions=w.depth.decisions,
                               ranges=f.ranges, kinds=f.kinds, material=w.material, planned=w.planned,
                               required=w.depth.required)
                f.trace_entry["unused"] = w.gate["unused_entries"]
            if w.gate["ok"]:
                with t.stage("hub"):
                    w.end_check = await _end_check(d, w)
    except JevError as ex:
        aborted, w.error = "jev", {"kind": "jev", "stage": "walk", "detail": str(ex)}
    except (BreadthError, EntryIncomplete) as ex:
        aborted, w.error = "hub_breadth", {"kind": "hub", "stage": "breadth", "detail": str(ex)}
    except StepLimit as ex:
        aborted, w.error = "C3", {"kind": "step_limit", "stage": "depth", "detail": str(ex)}
    except Exception as ex:  # noqa: BLE001 — 허브 오류 · build 불일치 등 — 그 열만 「실행 중단」
        aborted, w.error = "hub", {"kind": type(ex).__name__, "stage": "walk", "detail": str(ex)[:300]}
    if aborted is None and w.gate and not w.gate["ok"]:
        aborted = "gate:" + ",".join(w.gate["fails"])
    w.end_state, w.reason = es.decide(aborted=aborted, entry_end=w.front.end if w.front else None,
                                      unsettled=(w.end_check or {}).get("result"),
                                      required_ok=bool(w.depth and w.depth.required.get("ok")), gate_ok=True)
    if w.end_state in NO_B_ON:  # 「실행 중단」 의 verified 는 프롬프트에 한 행도 안 간다
        w.material = Material()
    return w


async def _end_check(d: Deps, w: Walk) -> dict:
    """(가) · (나) — 읽은 조회 → (가) 후보면 그 범위의 나머지 조회를 판정용으로(N-6 — coverage 셈도 함께 기록)."""
    f = w.front
    assert f is not None
    read = [r for r in w.reads if r.call.purpose == "read"]
    rows, lc = es.reads_rows(read), es.reads_lc(read)
    out: dict = {"needs_judgement": es.needs_judgement(rows, lc), "by": "query"}
    judge_rows: list[dict] = []
    if out["needs_judgement"]:
        ks = range_kinds(f.entries, f.ranges)
        nodes = [e.node for e in f.entries]
        out["coverage_zero"] = all(sum((f.coverage.get(n, {}).get(k) or {}).get(x) or 0
                                       for x in ("self", "lower", "inherited")) == 0 for n in nodes for k in ks)
        calls = plan_calls(f.entries, ks, purpose="end_state", groups=f.groups or None)
        before = len(w.reads)
        await read_calls(d.hub, calls, w.verified, w.ledger, w.reads, follow=False)
        judge = [r for r in w.reads[before:] if r.call.purpose == "end_state"]
        judge_rows = es.reads_rows(judge)
        out["judgement_calls"] = len(judge)
        out["query_zero"] = not es.counted_rows(judge_rows)
    out["result"] = es.unsettled_rows(rows, lc, judge_rows)
    return out


def judgement(w: Walk) -> dict:
    """단계 ⑤ 에 주는 앱 판정 — 코드 값 · 이름만(사람에게 보일 문장이 아니다)."""
    f = w.front
    unresolved = []
    if w.end_state == es.EXHAUSTED and w.depth:
        names = {e.node: e.name for e in (f.entries if f else [])}
        unresolved = [{"target": names.get(i["target"], ""), "kind": KIND_NAME[i["kind"]]}
                      for i in w.depth.required.get("items", []) if not i["ok"]]
    inherited = sorted({KIND_NAME[i["kind"]] for i in (w.depth.required.get("items", []) if w.depth else [])
                        if i.get("met_by") == "inherited"})
    read_as = [{"name": e.name, "by": e.read_as.get("by")} for e in (f.entries if f else []) if e.read_as]
    return {"end_state": w.end_state, "reason": w.reason, "scope_gap": bool(f and f.scope_gap),
            "kinds": [KIND_NAME[k] for k in (f.kinds if f else [])],
            "inherited_fill": inherited, "read_as": read_as,
            "offered": [{"kind": o["name"], "rows": o["rows"]} for o in (f.offered if f else [])]
            if w.end_state not in NO_B_ON else [],
            "ask_back": f.ask_back if f and w.end_state == es.NO_ENTRY else [],
            "unresolved": unresolved}


def prompt_pages(question: str, w: Walk, max_tokens: int, system: str) -> list[str]:
    head = "## 앱 판정\n" + json.dumps(judgement(w), ensure_ascii=False) + "\n\n"
    q = "## 질문\n" + question + "\n"
    if not w.groups:
        return [head + "## 자료\n(없음)\n\n" + q]
    rt = roster_text(w.material.roster)  # FT8 — 이름 목록은 첫 쪽 머리에 전부(O-33 — 쪽마다 몫은 이 판에서 안 함)
    names = ("## 이름 목록\n" + rt + "\n\n") if rt else ""
    overhead = est_tokens(system + head + names + q) + 64
    ps = pages(w.groups, max_tokens, overhead)
    n = len(ps)
    return [head + (names if i == 1 else "") + f"## 자료 (쪽 {i}/{n})\n" + to_lines(p) + "\n\n" + q
            for i, p in enumerate(ps, 1)]


def split_tail(text: str) -> tuple[str, list[dict] | None, int]:
    """답 → (꼬리 뗀 본문, answer_lines | None, 꼬리 글자 수). 꼬리가 없거나 한 줄이라도 꼴이 깨졌으면 None."""
    i = text.find(TAIL_MARK)
    if i < 0:
        return text.rstrip(), None, 0
    body, tail = text[:i].rstrip(), text[i + len(TAIL_MARK):]
    out = []
    for ln in tail.strip().splitlines():
        if not ln.strip():
            continue
        m = TAIL_LINE.match(ln)
        if not m:
            return body, None, len(text) - i
        rows: list[int] = []
        for part in m.group(2).split(","):
            a, _, b = part.partition("-")
            rows.extend(range(int(a), int(b) + 1) if b.strip() else [int(a)])
        out.append({"answer_line": int(m.group(1)), "rows": rows})
    return body, (out or None), len(text) - i


class _TailFilter:
    """스트리밍 delta 에서 꼬리 표식 뒤를 화면으로 흘리지 않는다(표식이 delta 둘에 걸쳐도)."""

    def __init__(self) -> None:
        self.buf = ""
        self.cut = False

    def __call__(self, ev: dict) -> dict | None:
        if ev.get("event") != "delta":
            return ev
        if self.cut:
            return None
        self.buf += ev.get("text") or ""
        j = self.buf.find(TAIL_MARK)
        if j >= 0:
            self.cut = True
            out, self.buf = self.buf[:j], ""
            return {**ev, "text": out} if out else None
        keep = 0
        for n in range(min(len(TAIL_MARK) - 1, len(self.buf)), 0, -1):
            if TAIL_MARK.startswith(self.buf[-n:]):
                keep = n
                break
        out = self.buf[:len(self.buf) - keep]
        self.buf = self.buf[len(self.buf) - keep:]
        return {**ev, "text": out} if out else None


async def answer(question: str, w: Walk, d: Deps, reasoning: str, effort: str | None = None) -> dict:
    """단계 ⑤ — 쪽마다 부르고 부분 답을 쪽 순서대로 잇는다(앱이 문장을 고치거나 고르지 않는다)."""
    system = d.system("b")
    try:
        texts = prompt_pages(question, w, d.settings.answer_input_max_tokens, system)
    except FoldTooLarge as ex:
        return {"answer": "", "error": {"kind": "fold", "stage": "llm", "detail": str(ex)}, "pages": [], "tokens": {},
                "timing": {}, "round_trips": 0, "observed_model": None, "fold_failed": True}
    parts, pg, toks, rt, err, model = [], [], [], 0, None, None
    lines: list[dict] | None = []
    tail_chars = 0
    line_off = 0
    t0 = time.perf_counter()
    ttft = None
    for i, p in enumerate(texts, 1):
        with progress.gate(_TailFilter()):
            r = await cli.run(d.spec(reasoning, effort), system=system, prompt=p,
                              max_concurrent=d.settings.cli_max_concurrent)
        rt += r["llm_round_trips"]
        model = model or r["observed_model"]
        if ttft is None and r["timing"].get("ttft_ms") is not None:
            ttft = round((time.perf_counter() - t0) * 1000 - (r["timing"]["total_ms"] - r["timing"]["ttft_ms"]), 1)
        pg.append({"page": i, "bytes": len(p.encode("utf-8")), "groups": p.count('"group":'),
                   "thinking_seen": r["thinking_seen"]})
        toks.append(r["tokens"])
        if r["error"]:
            err = {"kind": "cli", "stage": "llm", "detail": r["error"], "page": i}
            break
        body, al, tc = split_tail(r["answer"])
        tail_chars += tc
        if al is None or lines is None:
            lines = None
        else:
            lines.extend({"answer_line": x["answer_line"] + line_off, "rows": x["rows"]} for x in al)
        line_off += len([ln for ln in body.splitlines() if ln.strip()])
        parts.append(body)
    total = round((time.perf_counter() - t0) * 1000, 1)
    return {"answer": "" if err else "\n\n".join(parts), "error": err, "pages": pg, "tokens": toks,
            "timing": {"ttft_ms": ttft, "first_answer_text_ms": ttft, "total_ms": total}, "round_trips": rt,
            "observed_model": model, "input_bytes": sum(x["bytes"] for x in pg),
            "answer_lines": lines if (lines and not err) else None, "answer_tail_chars": tail_chars}


def b_trace(w: Walk, ans: dict) -> dict:
    f = w.front
    shown = w.end_state not in NO_B_ON
    return {
        "front": f.trace_front if f else {},
        "entry": f.trace_entry if f else {},
        "entry_pick": f.trace_entry_pick if f else None,
        "breadth": [r.trace() for r in w.reads if r.call.purpose in ("read", "end_state", "label")],
        "depth_reads": [r.trace() for r in w.reads if r.call.purpose == "depth"],
        "steps": w.depth.steps if w.depth else [],
        "depth_decisions": w.depth.decisions if w.depth else {},
        "required": w.depth.required if w.depth else {},
        "end_check": w.end_check,
        "visited": w.ledger.visited,
        "verified_gids": [v.as_trace() for v in w.verified.rows.values()] if shown else [],
        "verified_lines": w.material.lines if shown else [],
        "answer_lines": ans.get("answer_lines"),
        "answer_tail_chars": ans.get("answer_tail_chars", 0),
        "gates": w.gate or {},
        "llm_input_bytes": ans.get("input_bytes"),
        "llm_pages": ans.get("pages"),
        "end_reason": w.reason,
        "answer_check": ans.get("answer_check"),
    }


def _stage_ms(w: Walk, name: str) -> float:
    return round(sum((s["end_ms"] or 0) - s["start_ms"] for s in w.timer.stages if s["name"] == name), 1)


async def run(question: str, d: Deps, *, b_on: bool = True, effort: str | None = None,
              read_kinds: list[str] | None = None) -> ColumnOut:
    jev0, hub0 = d.jev.calls, d.hub.calls
    jms0 = getattr(d.jev, "ms_total", 0.0)
    hlog0 = len(d.hub.log)
    w = await walk(question, d, read_kinds=read_kinds)
    jev_ms = round(getattr(d.jev, "ms_total", 0.0) - jms0, 1)
    hub_ms = round(sum(x.get("ms") or 0 for x in d.hub.log[hlog0:]), 1)
    walk_ms = w.timer.clock.ms()
    progress.emit("step", step="⑤", stage="llm", end_state=w.end_state)
    ans = await answer(question, w, d, d.settings.answer_reasoning, effort)
    if ans.get("fold_failed"):  # W-4 · FT5 — 쪽 나눔으로도 못 실음 → 「실행 중단」 · 빈 재료로 단계 ⑤
        w.error = w.error or ans["error"]
        w.end_state, w.reason, w.material = es.ABORTED, "FT5", Material()
        ans = await answer(question, w, d, d.settings.answer_reasoning, effort)
    if w.material.roster and not ans.get("error"):  # CP4 — 기록만(gate 아님 · 답 · 종료 상태를 고치지 않는다)
        ans["answer_check"] = answer_check(ans["answer"], w.material.roster)
    w.timer.stages.append({"name": "llm", "start_ms": walk_ms, "end_ms": round(walk_ms + ans["timing"].get("total_ms", 0), 1)})
    w.timer.total_ms = round(walk_ms + ans["timing"].get("total_ms", 0), 1)
    if ans["timing"].get("ttft_ms") is not None:
        w.timer.ttft_ms = w.timer.first_answer_ms = round(walk_ms + ans["timing"]["ttft_ms"], 1)  # 도구 없음 — 두 값 같다
    err = w.error or ans["error"]
    if w.error and ans["error"] and w.error is not ans["error"]:
        err = {**w.error, "llm": ans["error"]}
    timing = w.timer.as_dict()  # parts = 속도 탭 단계 막대 — 판정 · 채점에는 안 쓴다
    timing["parts"] = {"jev_ms": jev_ms, "hub_ms": hub_ms, "llm_ms": round(ans["timing"].get("total_ms", 0) or 0, 1)}
    res = column(ans["answer"], end_state=w.end_state, timing=timing,
                 tokens={"llm": ans["tokens"]},
                 calls={"llm_round_trips": ans["round_trips"], "tool_calls": 0, "hub_calls": d.hub.calls - hub0,
                        "jev_calls": d.jev.calls - jev0},
                 cited=cited_gids(ans["answer"]), error=err, trace=b_trace(w, ans))
    on = None
    if b_on:  # 화면 알림 — B 본 꼴이 먼저 끝났다(runner 의 column_done 은 같은 값으로 한 번 더 온다)
        progress.emit("column_done", result=res, model=ans["observed_model"],
                      effort=d.spec(d.settings.answer_reasoning, effort).effort_arg())
    if b_on and w.end_state not in NO_B_ON:
        on_effort = d.spec("on", effort).effort_arg()
        with progress.column("b_on"):
            progress.emit("start", effort=on_effort)
            a2 = await answer(question, w, d, "on", effort)
            on = {"answer": a2["answer"], "timing": a2["timing"], "tokens": {"llm": a2["tokens"]},
                  "calls": {"llm_round_trips": a2["round_trips"]}, "cited": cited_gids(a2["answer"]),
                  "answer_lines": a2.get("answer_lines"), "error": a2["error"]}
            progress.emit("column_done", result=on, model=a2["observed_model"], effort=on_effort)
    elif b_on:
        progress.emit("column_done", column="b_on", result=None, model=None, effort=None)
    return ColumnOut(res, ans["observed_model"], {"b_on": on, "build": w.build(),
                                                  "jev_models": sorted(d.jev.models)})


__all__ = ["NO_B_ON", "TAIL_MARK", "Walk", "answer", "judgement", "prompt_pages", "range_name", "run", "split_tail",
           "walk"]
