"""한 턴 흐름(SPEC-004 §4-2 TF2 ~ TF6 · §4-3 · WORK-011 §12 · 검수 W-6 · W-8 · N-6).

걷기(시험 앱 B 와 같은 `walk()` — 앞 턴 대상만 `carry` · Jev 지시는 상담 프롬프트의 `jev_front`) → 재료(대화 번호 · 덧붙이기)
→ 메시지 크기 확인(넘으면 `FT5` 실행 중단 — `list` 를 보내기 **전에** 잰다 · N-6) → `list` · `sites`(LLM 을 기다리지 않음)
→ 실행기 한 번(`delta` 는 꼬리 표식 앞까지만) → `done`(꼬리 뗀 본문) 또는 `turn_error` → 맺는 줄.
턴은 구독자와 무관하게 서버 작업으로 끝까지 돈다(W-6) · 취소 라우트는 없다.

0.2(SPEC-005 §3-1 L1 ~ L9 · WORK-012 §5): 대화가 켠 상태(`Conv.consult_state == "on"`)일 때만 `_Consult` 가 끼어든다 —
L2 든 후보 뒤에 걸린 선택지 · L3 거둠 요청을 걷기와 함께 · L4 객체 갱신(세 종료 상태 턴에도) · L5 객체 기준 조회 · 시너지 ·
L7 빈 칸 하나 · 재료 뒤 요약 줄 · L6 객체 기준 `list` · `sites`(바뀐 것이 있을 때만 · 세 종료 상태 턴에는 안 보냄) ·
L9 장부 `consult{…}`. 끈 상태는 0.1 의 줄 그대로 돈다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from clinic_chat import progress
from clinic_chat.columns.base import Deps
from clinic_chat.columns.col_b import NO_B_ON, b_trace, walk
from clinic_chat.config import Settings
from clinic_chat.consult import blanks as blanks_mod
from clinic_chat.consult import carry as carry_mod
from clinic_chat.consult import material as mat
from clinic_chat.consult import plan as plan_mod
from clinic_chat.consult import state as cst
from clinic_chat.consult import synergy as syn_mod
from clinic_chat.consult.ledger import Conv, Ledger
from clinic_chat.consult.listing import build_list, list_codes, state_list
from clinic_chat.consult.prompt import file_sha
from clinic_chat.consult.render import Render
from clinic_chat.consult.sites import build_sites, place
from clinic_chat.consult.steps import Steps
from clinic_chat.consult.tail import TailFilter, split_tail
from clinic_chat.llm.chat import ChatLLM
from clinic_chat.runs import app_commit
from clinic_chat.walk import end_state as es
from clinic_chat.walk.material import Material
from clinic_chat.walk.ranges import OFFER_NAME

Push = Callable[[str, str], None]


@dataclass
class Ctx:
    settings: Settings
    ledger: Ledger
    render: Render
    llm: ChatLLM
    deps_factory: Callable[[Settings], Deps] = Deps.from_settings
    extra: dict[str, Any] = field(default_factory=dict)


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def targets_of(w) -> list[dict]:
    f = w.front
    out = []
    for e in (f.entries if f else []):
        if e.table == OFFER_NAME:
            continue
        x = {"table": e.table, "code": e.code, "name": e.name, "by": e.by}
        if e.by == "carried" and e.read_as:
            x["read_as"] = {"by": "carry", "from_turn": e.read_as.get("from_turn")}
        out.append(x)
    return out


async def run_turn(ctx: Ctx, conv: Conv, n: int, question: str, push: Push, *, clicked_at: float | None = None) -> None:
    s, r = ctx.settings, ctx.render
    t0 = clicked_at or time.perf_counter()
    prompt = conv.prompt
    assert prompt is not None
    d = ctx.deps_factory(s)
    jms0, hlog0 = getattr(d.jev, "ms_total", 0.0), len(d.hub.log)
    timing: dict[str, Any] = {"first_list_ms": None, "ttft_ms": None, "total_ms": None, "parts": {}}

    steps = Steps()
    shown = {"text": ""}  # 지금까지 화면에 보낸 답 글(꼬리 앞까지) — `delta` 는 늘 이 전체를 마크다운으로 다시 그린다

    def push_steps() -> None:
        push("step", r.steps(steps.view()))

    def sink(ev: dict) -> None:
        kind = ev.get("event")
        if kind == "step":
            if steps.raw(str(ev.get("stage") or ev.get("step") or "")):
                push_steps()
        elif kind == "delta" and ev.get("text"):
            if timing["ttft_ms"] is None:
                timing["ttft_ms"] = _ms(t0)
            shown["text"] += ev["text"]
            if shown["text"].strip():
                push("delta", r.delta(shown["text"]))

    carried_in = carry_mod.candidates(conv.turns, s.chat_carry_turns)
    cs = Consult.begin(ctx, conv, n, question, d, carried_in) if conv.consult_state == "on" else None
    with progress.listen(sink):
        if cs is None:
            w = await walk(question, d, carry=carried_in or None, instr=prompt.jev_front)
        else:  # L2 · L3 — 걸린 선택지를 든 후보 뒤에 · 거둠 요청은 걷기와 함께(걷기를 기다리게 하지 않는다)
            w, _ = await asyncio.gather(walk(question, d, carry=cs.carry or None, instr=prompt.jev_front), cs.retract())
    walk_ms = _ms(t0)
    row: dict[str, Any] = {"turn": n, "asked_at": conv.starts[n]["asked_at"], "question": question}
    build = w.build()
    gh = (build or {}).get("gold_hash")
    err: dict | None = w.error
    conv_end = None
    llm_res: dict | None = None
    tm = mat.TurnMaterial(next_n=conv.next_n)
    lst = sites = None
    msg = ""
    if conv.gold_hash and gh and gh != conv.gold_hash:  # CL14 · C-20 — 앞 턴 재료의 줄이 다른 판의 것
        err, conv_end = {"kind": "build_changed", "stage": "walk", "detail": None}, "build_changed"
    else:
        if w.end_state not in NO_B_ON:
            tm = mat.renumber(w, conv.sent, conv.summaries, conv.next_n)
        if cs is not None:  # L4 · L5 · L7 — 객체 갱신 · 객체 기준 조회 · 시너지 · 빈 칸(메시지보다 먼저)
            await cs.after_walk(w)
            msg = cs.message(w, tm)
        else:
            msg = mat.message(prompt, mat.judgement_of(w, tm, n), tm, question)
        if mat.too_large(prompt, msg, s.answer_input_max_tokens, cs.system_text if cs else None):  # AP8 · C-21
            w.error = w.error or {"kind": "fold", "stage": "material", "detail": None}
            w.end_state, w.reason, w.material = es.ABORTED, "FT5", Material()
            err = w.error
            tm = mat.TurnMaterial(next_n=conv.next_n)
            if cs is not None:  # 넘으면 `ask` · 시너지 줄도 뺀다(WORK-012 §5)
                cs.drop_extras()
                msg = cs.message(w, tm)
            else:
                msg = mat.message(prompt, mat.judgement_of(w, tm, n), tm, question)
        if cs is not None:
            if w.end_state not in NO_B_ON:  # L6 — 객체 기준 조각 · 바뀐 것이 있을 때만(SC3) · 세 종료 상태 턴은 안 보냄(W-6)
                lst, sites = cs.screen(push, r)
                if lst is not None:
                    timing["first_list_ms"] = _ms(t0)
                    if steps.listed():
                        push_steps()
                if sites is not None:
                    push("sites", r.sites(sites))
        elif w.end_state not in NO_B_ON:  # TF3 — LLM 을 기다리지 않고 먼저
            names = {e.node.split(":", 1)[1]: e.name for e in (w.front.entries if w.front else [])
                     if e.table == "concern"}
            lst = build_list(w.reads, names)
            sites = build_sites(w.reads)
            if lst is not None:
                push("list", r.list(lst))
                timing["first_list_ms"] = _ms(t0)
                if steps.listed():
                    push_steps()
            if sites is not None:
                push("sites", r.sites(sites))
        history = [{"user_message": t.get("llm_user_message"), "answer_raw": t.get("answer_raw")}
                   for t in conv.turns if (t.get("llm") or {}).get("answered")]
        llm_prompt = dataclasses.replace(prompt, system_text=cs.system_text) if cs is not None else prompt
        with progress.listen(sink), progress.gate(TailFilter(prompt.tail_marker)):
            llm_res = await ctx.llm.turn(conv.chat, msg, llm_prompt, history)
        if llm_res["error"]:
            err = {**err, "llm": llm_res["error"]} if err else llm_res["error"]
            if conv.chat.state == "broken":
                conv_end = "broken"
    raw = (llm_res or {}).get("answer_raw") or ""
    body, lines, tail_chars = split_tail(raw, prompt.tail_marker)
    llm_err = (llm_res or {}).get("error")
    if steps.finish():
        push_steps()
    if llm_res is None or llm_err:
        kind = (llm_err or err or {}).get("kind") or "internal"
        push("turn_error", r.error(n, kind))
    else:
        push("done", r.answer(n, body, w.end_state))
    timing["total_ms"] = _ms(t0)
    timing["parts"] = {"jev_ms": round(getattr(d.jev, "ms_total", 0.0) - jms0, 1),
                       "hub_ms": round(sum(x.get("ms") or 0 for x in d.hub.log[hlog0:]), 1),
                       "llm_ms": ((llm_res or {}).get("timing") or {}).get("total_ms"), "walk_ms": walk_ms}
    llm = dict((llm_res or {}).get("llm") or {})
    llm.update({"observed_model": (llm_res or {}).get("observed_model"),
                "observed_models": (llm_res or {}).get("observed_models"), "error": llm_err,
                "answered": llm_res is not None and not llm_err})
    now_sha = file_sha(ctx.settings.chat_prompt_path())
    row.update({
        "llm": llm, "session_state": conv.chat.state,
        "session_next": {"session_id": conv.chat.session_id},
        "targets": targets_of(w), "carried": (w.front.carried if w.front else []),
        "kinds": list(w.front.kinds) if w.front else [], "end_state": w.end_state, "reason": w.reason,
        "trace": b_trace(w, {"answer_lines": lines, "answer_tail_chars": tail_chars,
                             "input_bytes": len(msg.encode("utf-8"))}),
        "material": tm.ledger() if err is None else {**tm.ledger(), "not_counted": True},
        "list": lst, "sites": sites, "llm_user_message": msg,
        "answer": body if not llm_err else "", "answer_raw": raw, "answer_lines": lines,
        "timing": timing, "tokens": (llm_res or {}).get("tokens") or {},
        "thinking_seen": (llm_res or {}).get("thinking_seen"), "build": build,
        "prompts": {"version": prompt.version, "consult_at_start": conv.prompt_sha, "consult_now": now_sha,
                    "changed": bool(now_sha and conv.prompt_sha and now_sha != conv.prompt_sha)},
        "commits": {"app": app_commit()}, "error": err, "conv_end": conv_end,
    })
    if cs is not None:  # L9 — 장부 `consult{…}` · `carried[]` 는 앞 턴 대상만(N-8 · N-3)
        row["carried"], row["consult"] = cs.ledger(w, err, conv_end)
    ctx.ledger.end(conv, row)


class Consult:
    """0.2 켠 상태의 한 턴 몫(SPEC-005 §3-1 L2 ~ L9). 객체는 복사본으로 짓고 맺는 줄의 `consult.after` 에 적는다 —
    `Conv.consult` 는 `ledger._apply_end` 가 오류 없는 줄일 때만 받는다(RV9)."""

    def __init__(self, ctx: Ctx, conv: Conv, n: int, question: str, d: Deps, carried_in: list[dict]) -> None:
        self.ctx, self.conv, self.n, self.question, self.d = ctx, conv, n, question, d
        self.s = ctx.settings
        self.prompt = conv.prompt
        self.obj = cst.snapshot(conv.consult or cst.empty())
        self.prev_targets = {(x["table"], x["code"]) for x in carried_in}
        self.carry = carry_mod.with_options(carried_in, self.obj["asked"], n)  # L2 · UP4
        self.system_text = self.prompt.system_text_state or self.prompt.system_text
        self.rt: dict | None = None
        self.diff: list[dict] = []
        self.blanks: list[dict] = []
        self.ask: dict | None = None
        self.ask_why: str | None = None
        self.ask_view: dict | None = None
        self.syn_lines: list[int] = []
        self.syn: dict = {"lines": [], "bundles": [], "diff": []}
        self.plan = plan_mod.Plan()
        self.queries: list[dict] = []
        self.list_state: dict | None = None
        self.sent_list: dict | None = None
        self.sent_sites: dict | None = None
        self.prev_ask: dict | None = None
        self.error: dict | None = None
        self.skipped: str | None = None
        self.ms: dict[str, float] = {}

    @classmethod
    def begin(cls, ctx: Ctx, conv: Conv, n: int, question: str, d: Deps, carried_in: list[dict]) -> Consult:
        return cls(ctx, conv, n, question, d, carried_in)

    async def retract(self) -> None:
        """RV2 — 객체에 항목이 있는 턴에만(`CHAT_RETRACT=on`) · 실패해도 턴은 계속(B-5)."""
        if self.s.chat_retract != "on" or not cst.has_items(self.obj):
            return
        self.rt = await cst.retract(self.question, self.obj, self.d, self.prompt.jev_state["retract"],
                                    yes_at=self.s.jev_noul_yes_at)

    async def after_walk(self, w) -> None:
        if self.conv.gold_hash and (w.build() or {}).get("gold_hash") not in (None, self.conv.gold_hash):
            self.skipped = "build_changed"
            return
        t0 = time.perf_counter()
        last = self.obj["asked"][-1] if self.obj["asked"] else None
        prev_ask = last if last and last.get("turn") == self.n - 1 else None
        obs, kinds = cst.observe(w, prev_ask)
        subjects_before = {cst.node(x) for x in self.obj["subjects"]}
        self.diff = cst.update(self.obj, obs, kinds, self.n, cst.retract_yes(self.rt))  # L4(세 종료 상태 턴에도 — W-6)
        self.prev_ask = cst.mark_answered(self.obj, self.n, self.diff, subjects_before)
        self.ms["update_ms"] = _ms(t0)
        terminal = w.end_state in NO_B_ON
        reader = plan_mod.Reader(self.d.hub, self.conv.memo, w.reads, self.queries)
        t1 = time.perf_counter()
        try:
            if terminal:  # 그 턴에 필요해진 조회는 다음 턴이 따라잡는다(W-6)
                plan_mod.derive(self.obj, self.conv.memo)
            else:
                self.plan = await plan_mod.run(self.obj, reader)
                names = {x["code"]: x["name"] for x in self.obj["concerns"]}
                base = build_list(self.plan.lists, names, codes=True) if self.plan.lists else None
                procs, mods = list_codes(base)
                self.syn = await syn_mod.run(self.obj, reader, self.n, procs, mods, on=self.s.chat_synergy == "on")
                self.diff += self.syn["diff"]
                self.list_state = state_list(self.obj, self.plan.lists, names, self.syn["bundles"],
                                             syn_mod.marks(self.syn["bundles"], self.obj))
        except Exception as e:  # noqa: BLE001 — 객체 조회가 깨져도 걷기의 답은 낸다(기록만)
            self.error = {"kind": type(e).__name__, "detail": str(e)[:200]}
        self.ms["plan_ms"] = _ms(t1)
        new_nodes = {f"{x['table']}:{x['code']}" for x in self.diff if x["op"] == "add" and x["table"]}
        new_nodes |= {f"concern:{x['to']}" for x in self.diff if x["op"] == "focus" and x["to"]}
        new_guess = {f"{x['table']}:{x['code']}" for x in self.diff if x["op"] == "add" and x["to"] == "guess"}
        list_shown = bool(self.list_state and self.list_state["concerns"])
        self.blanks = blanks_mod.count(self.obj, self.plan, list_shown)  # L7
        self.ask, self.ask_why = blanks_mod.pick(
            self.blanks, self.obj, self.n, new_nodes=new_nodes, new_guess=new_guess, prev_ask=self.prev_ask,
            end_state=w.end_state, kinds_read=list(w.front.kinds) if w.front else [], ask_max=self.s.chat_ask_max)

    def _summaries(self, tm: mat.TurnMaterial) -> None:
        """재료 뒤에 객체 조회의 요약 줄 — QY3 · QY7 선택지(그 턴에 BK2 로 물을 때만) · SY4 시너지(리스트 안 상대만)."""
        self.syn_lines, self.ask_view = [], None
        if self.ask is not None:
            opt_n: dict[str, int] = {}
            if self.ask["bk"] in ("BK2", "BK2s"):
                raw = {o["code"]: o for o in self.plan.options.get(self.ask["about"], [])}
                rows = []
                for o in self.ask["options"]:
                    r = raw.get(o["code"]) or {}
                    gids = r.get("gids") or []
                    key = mat.summary_key(f"{self.ask['bk']}|{self.ask['about']}", o["code"], gids)
                    rows.append((key, gids, {"option": True, "concern_name": o["name"], "layer": o["layer"],
                                             "holder": r.get("holder") or "", "rows": len(gids)}))
                rule = "QY3" if self.ask["bk"] == "BK2" else "QY7"
                ns = mat.add_summaries(tm, rule, {"about": self._name(self.ask["about"])}, rows,
                                       self.conv.summaries)
                opt_n = {o["code"]: k for o, k in zip(self.ask["options"], ns, strict=True)}
            self.ask_view = {"slot": self.ask["slot"], "about": self._name(self.ask["about"]),
                             "options": [{"name": o["name"], "layer": o["layer"],
                                          **({"n": opt_n[o["code"]]} if o["code"] in opt_n else {})}
                                         for o in self.ask["options"]]}
        rows = []
        for ln in self.syn["lines"]:
            p = ln["partner"]
            rows.append((syn_mod.line_key(ln["a"], p), p["gids"],
                         {"synergy": True, "with": ln["a_name"], "name": p["name"], "table": p["table"],
                          "a_layer": p["a_layer"], "rows": p["rows"], "same_day": p["same_day"],
                          "goal_effects": p["goal_effects"], "same_day_caution": p["caution"]}))
        if rows:
            self.syn_lines = mat.add_summaries(tm, "QY4", {}, rows, self.conv.summaries)

    def _name(self, about: str) -> str:
        table, _, code = about.partition(":")
        it = cst.find(self.obj, table, code)
        return (it or {}).get("name") or ""

    def message(self, w, tm: mat.TurnMaterial) -> str:
        if w.end_state not in NO_B_ON:
            self._summaries(tm)
        self.tm = tm
        j = mat.judgement_of(w, tm, self.n, {"ask": self.ask_view, "synergy_lines": self.syn_lines})
        return mat.message(self.prompt, j, tm, self.question, mat.consult_view(self.obj))

    def drop_extras(self) -> None:
        self.ask, self.ask_why, self.ask_view, self.syn_lines = None, "FT5", None, []
        self.syn = {**self.syn, "lines": []}

    def screen(self, push: Push, r: Render) -> tuple[dict | None, dict | None]:
        """L6 — 묶음 ① · ② · ③ 가운데 하나라도 바뀌었으면 `list`(SC3) · `focus` 고민의 고민 부위로 `sites`(SC5)."""
        lst = self.list_state
        prev = self.conv.last_list()
        empty_now = not lst or not (lst["interest"] or lst["concerns"] or lst["synergy"])
        if lst is not None and lst != prev and not (prev is None and empty_now):
            push("list", r.list(lst))
            self.sent_list = lst
            f = self.obj.get("focus")
            c = lst["concerns"][0] if lst["concerns"] else None
            if c is not None:
                self.obj["shown"]["lists"].append({"concern_code": c["concern_code"], "site_code": None, "turn": self.n,
                                                   "counts": c["counts"],
                                                   "inherited_from": (c.get("inherited_from") or {}).get("concern_code")})
                self.diff.append({"op": "shown", "slot": "shown.lists", "table": "concern", "code": f, "from": None,
                                  "to": self.n, "why": "list"})
        pairs = [{"concern_code": x["concern_code"], "site_code": x["code"], "site_name": x["name"],
                  "csi_id": x.get("csi_id")} for x in self.obj["concern_sites"] if x["concern_code"] == self.obj.get("focus")]
        sites = place(pairs)
        if self.sent_list is not None or sites != self.conv.last_sites():
            self.sent_sites = sites
        return self.sent_list, self.sent_sites

    def _info(self, w) -> None:
        tm = getattr(self, "tm", None)
        if tm is None:
            return
        by_kind: dict[str, list[int]] = {}
        for ln in tm.new_lines:
            if not ln.get("summary") and ln.get("kind"):
                for k in (ln["kind"] if isinstance(ln["kind"], list) else [ln["kind"]]):
                    by_kind.setdefault(str(k), []).append(ln["n"])
        nodes = [e.node for e in (w.front.entries if w.front else []) if e.table in cst.SLOT_OF]
        for k, lines in by_kind.items():
            for nd in nodes:
                self.obj["shown"]["info"].append({"node": nd, "kind": k, "turn": self.n, "lines": lines})

    def ledger(self, w, err: dict | None, conv_end: str | None) -> tuple[list[dict], dict]:
        rows = list(w.front.carried if w.front else [])
        carried = [{k: v for k, v in x.items() if k != "origin"} for x in rows
                   if x.get("origin") != "option" or (x["table"], x["code"]) in self.prev_targets]
        opts = [{k: x[k] for k in ("table", "code", "from_turn", "noul", "decision")} for x in rows
                if x.get("origin") == "option"]
        if self.skipped:
            return carried, {"skipped": self.skipped, "diff": [], "after": None}
        if err is None:
            self._info(w)
            if self.ask is not None:
                blanks_mod.record(self.obj, self.ask, self.n)
                self.diff.append({"op": "asked", "slot": "asked", "table": None, "code": self.ask["slot"], "from": None,
                                  "to": self.ask["about"], "why": self.ask_why})
        out = {"diff": self.diff, "after": self.obj if err is None else None,
               "blanks": blanks_mod.ledger_view(self.blanks),
               "ask": ({k: self.ask[k] for k in ("slot", "about", "options", "why")} if self.ask else None),
               "ask_why": self.ask_why, "options_carried": opts,
               "retract": (self.rt or {}).get("items") or [], "retract_ms": (self.rt or {}).get("ms"),
               "retract_resolve_ms": (self.rt or {}).get("resolve_ms"),
               "retract_requests": (self.rt or {}).get("requests", 0), "queries": self.queries,
               "synergy_lines": self.syn_lines, "jev_requests": {
                   "front": (w.front.trace_front or {}).get("jev_requests") if w.front else None,
                   "entry_pick": ((w.front.trace_entry_pick or {}).get("requests") if w.front else None),
                   "retract": (self.rt or {}).get("requests", 0)},
               "hub_queries": len(self.queries) - sum(1 for q in self.queries if q.get("skipped"))
               + (1 if self.rt and self.rt.get("resolve_ms") is not None else 0), **self.ms}
        if (self.rt or {}).get("error"):
            out["retract_error"] = self.rt["error"]
        if self.error:
            out["error"] = self.error
        if err is not None:
            out["not_counted"] = True
        return carried, out
