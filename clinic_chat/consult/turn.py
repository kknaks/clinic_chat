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

import dataclasses
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from clinic_chat import progress
from clinic_chat.columns.base import Deps
from clinic_chat.columns.col_b import NO_B_ON, Walk, b_trace, walk
from clinic_chat.config import Settings
from clinic_chat.consult import blanks as blanks_mod
from clinic_chat.consult import carry as carry_mod
from clinic_chat.consult import close as close_mod
from clinic_chat.consult import entry_list as el_mod
from clinic_chat.consult import material as mat
from clinic_chat.consult import material_read as mr_mod
from clinic_chat.consult import plan as plan_mod
from clinic_chat.consult import site_filter as sf_mod
from clinic_chat.consult import site_index as si_mod
from clinic_chat.consult import state as cst
from clinic_chat.consult import synergy as syn_mod
from clinic_chat.consult import turn_kind as tk
from clinic_chat.consult import understand as und
from clinic_chat.consult import verify as vf_mod
from clinic_chat.consult import voice as voice_mod
from clinic_chat.consult.ledger import Conv, Ledger
from clinic_chat.consult.listing import build_list, list_codes, state_list
from clinic_chat.consult.prompt import file_sha
from clinic_chat.consult.render import Render, state_view
from clinic_chat.consult.sites import build_sites, place, site_pairs, viewable_subs
from clinic_chat.consult.steps import Steps
from clinic_chat.consult.tail import TailFilter, split_close, split_tail
from clinic_chat.consult.verify import Accepted
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
        elif e.read_as and e.read_as.get("by") == "abbr+carry":  # 0.3 검수 W-3 ⓐ
            x["read_as"] = {"by": "abbr+carry"}
        out.append(x)
    return out


async def run_turn(ctx: Ctx, conv: Conv, n: int, question: str, push: Push, *, clicked_at: float | None = None,
                   viewing: str | None = None) -> None:
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

    cs = Consult.begin(ctx, conv, n, question, d, viewing) if conv.consult_state == "on" else None
    with progress.listen(sink):
        if cs is None:  # 끈 가지(0.1 그대로 — CP7: 0.4 서버에서 새로 열지 않는다 · 옛 끈 대화는 `blocked` 로 막힌다)
            carried_in = carry_mod.candidates(conv.turns, s.chat_carry_turns)
            w = await walk(question, d, carry=carried_in or None, instr=prompt.jev_front)
        else:  # SPEC-008 ① ~ ⑤(나) — 이해 호출 하나 → 검증 → 객체 → 답 재료
            w = await cs.understand(sink)
    talk = cs is not None and cs.kind == "talk"
    walk_ms = _ms(t0)
    row: dict[str, Any] = {"turn": n, "asked_at": conv.starts[n]["asked_at"], "question": question}
    build = cs.build(w) if cs is not None else w.build()
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
        if w.end_state not in NO_B_ON and not talk:
            tm = mat.renumber(w, conv.sent, conv.summaries, conv.next_n, v3=cs is not None)
        if talk:  # TT1 ~ TT3 — 걷기 없이 객체 갱신 · 바뀌었을 때만 객체 기준 조회 · 새 재료 줄 0
            await cs.after_talk()
            msg = cs.message(w, tm)
        elif cs is not None:  # L4 · L5 · L7 — 객체 갱신 · 객체 기준 조회 · 시너지 · 빈 칸(메시지보다 먼저)
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
            if (cs.changed if talk else w.end_state not in NO_B_ON):  # L6 — 바뀐 것이 있을 때만(SC3 · TT2 · W-6)
                lst, sites = cs.screen(push, r)
                if lst is not None:
                    timing["first_list_ms"] = _ms(t0)
                    if steps.listed():
                        push_steps()
                if sites is not None:
                    push("sites", r.sites(sites, conv.conv_id))
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
    raw_tail, said = split_close(raw, prompt.tail_marker) if cs is not None else (raw, None)  # 0.3 CS5
    body, lines, tail_chars = split_tail(raw_tail, prompt.tail_marker)
    if cs is not None:
        cs.close["said"] = said
    llm_err = (llm_res or {}).get("error")
    if cs is not None and llm_res is not None and not llm_err:
        cs.check_voice(body)
    if steps.finish():
        push_steps()
    consult_row = cs.ledger(w, err, conv_end) if cs is not None else None  # L9 — `asked` · `shown` 까지 맺은 값(SV8)
    if r.panel:  # 0.3 SV8 — `state` 이벤트는 `done` 바로 앞(끈 상태 · 오류 턴은 빈 묶음)
        push("state", r.state_turn(state_view({"turn": n, "error": err,
                                               "consult": consult_row[1] if consult_row else None}, open_=True)))
    if llm_res is None or llm_err:
        kind = (llm_err or err or {}).get("kind") or "internal"
        push("turn_error", r.error(n, kind))
    else:
        push("done", r.answer(n, body, w.end_state))
    timing["total_ms"] = _ms(t0)
    hub_ms = round(sum(x.get("ms") or 0 for x in d.hub.log[hlog0:]), 1)
    llm_ms = ((llm_res or {}).get("timing") or {}).get("total_ms")
    timing["parts"] = ({"understand_ms": cs.understand_ms, "hub_ms": hub_ms, "llm_ms": llm_ms,
                        "material_ms": cs.material_ms} if cs is not None else
                       {"jev_ms": round(getattr(d.jev, "ms_total", 0.0) - jms0, 1), "hub_ms": hub_ms, "llm_ms": llm_ms,
                        "walk_ms": walk_ms})
    llm = dict((llm_res or {}).get("llm") or {})
    llm.update({"observed_model": (llm_res or {}).get("observed_model"),
                "observed_models": (llm_res or {}).get("observed_models"), "error": llm_err,
                "answered": llm_res is not None and not llm_err})
    now_sha = file_sha(ctx.settings.chat_prompt_path())
    if cs is None:
        trace = b_trace(w, {"answer_lines": lines, "answer_tail_chars": tail_chars,
                            "input_bytes": len(msg.encode("utf-8"))})
    elif talk:  # X-7 — `talk` 턴은 짧은 꼴
        trace = {"understand": {"kind": cs.kind, "ms": cs.understand_ms}, "answer_lines": lines,
                 "answer_tail_chars": tail_chars, "llm_input_bytes": len(msg.encode("utf-8"))}
    else:  # §6-1 — `trace.entry` · `trace.entry_pick` 없음 · `trace.depth` 새 꼴
        trace = b_trace(w, {"answer_lines": lines, "answer_tail_chars": tail_chars,
                            "input_bytes": len(msg.encode("utf-8"))})
        for k in ("entry", "entry_pick", "steps", "depth_decisions"):
            trace.pop(k, None)
        trace["depth"] = mr_mod.depth_trace(w)
    row.update({
        "llm": llm, "session_state": conv.chat.state,
        "session_next": {"session_id": conv.chat.session_id},
        "targets": cs.targets() if cs is not None else targets_of(w),
        "carried": [] if cs is not None else (w.front.carried if w.front else []),
        "kinds": list(w.front.kinds) if w.front else [], "end_state": w.end_state, "reason": w.reason,
        "trace": trace,
        "material": tm.ledger() if err is None else {**tm.ledger(), "not_counted": True},
        "list": lst, "sites": sites, "llm_user_message": msg,
        "answer": body if not llm_err else "", "answer_raw": raw, "answer_lines": lines,
        "timing": timing, "tokens": (llm_res or {}).get("tokens") or {},
        "thinking_seen": (llm_res or {}).get("thinking_seen"), "build": build,
        "prompts": {"version": prompt.version, "consult_at_start": conv.prompt_sha, "consult_now": now_sha,
                    "changed": bool(now_sha and conv.prompt_sha and now_sha != conv.prompt_sha)},
        "commits": {"app": app_commit()}, "error": err, "conv_end": conv_end,
    })
    if consult_row is not None:  # L9 — 장부 `consult{…}`(켠 턴의 `carried[]` = [] — AR-9)
        row["carried"], row["consult"] = consult_row
    ctx.ledger.end(conv, row)


class Consult:
    """켠 상태의 한 턴 몫(SPEC-008 §2 — rev 4). 객체는 복사본으로 짓고 맺는 줄의 `consult.after` 에 적는다 —
    `Conv.consult` 는 `ledger._apply_end` 가 오류 없는 줄일 때만 받는다(RV9).

    ① 목록 적재 → ② 이해 호출 하나 → ③ 검증(`Accepted` — 그 턴의 유일한 입구 · VF12) → ④ 객체 갱신 · 부위 거르개 →
    ⑤ 답 재료(`material_read.build_walk` — 걷기의 결정론 함수) · 객체 기준 조회 → ⑥ 화면 → ⑦ 빈 칸 → ⑧ 답(0.3) → ⑨ 장부.
    걷기 `walk()` · `front()` · Jev · 허브 `resolve` 맞춤 꼴 · 거둠 요청을 부르지 않는다(CP2). 실패(FL1)는 대상 없이 되묻는다."""

    def __init__(self, ctx: Ctx, conv: Conv, n: int, question: str, d: Deps, viewing: str | None = None) -> None:
        self.ctx, self.conv, self.n, self.question, self.d = ctx, conv, n, question, d
        self.s = ctx.settings
        self.prompt = conv.prompt
        self.obj = cst.snapshot(conv.consult or cst.empty())
        self.focus0 = self.obj.get("focus")
        self.viewing = viewing  # 0.3 FD6 — 누른 고민(폼 값)
        self.prev_row = sf_mod.prev_ok(conv.turns)  # 바로 앞 성공 턴(VF6 · OU7)
        self.kind = "new"
        self.follow: dict | None = None  # 장부 `consult.follow_target`
        self.changed = False  # `talk` 턴에 객체가 바뀌었나(TT2)
        self.system_text = self.prompt.system_text_v3 or self.prompt.system_text  # 0.3 VO1 — 켠 상태 = `system_v3` 통째
        self.turn_kind: dict = {"kind": "new", "from": "understand"}  # 0.4 — 장부 `consult.turn_kind{kind, from}`
        self.close: dict = {"by": None}  # 0.3 CS1 — 장부 `consult.close`
        self.close_view: dict = {"by": None}
        self.empty: list[dict] = []
        self.empty_view: list[dict] = []
        self.prev_view: dict | None = None
        self.site_view: dict | None = None
        self.voice: dict | None = None
        self.viewing_rec: dict | None = None
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
        self.el = None  # 이번 턴 목록(`entry_list.EntryList`)
        self.raw: und.Raw | None = None
        self.parsed: dict | None = None
        self.acc: Accepted | None = None
        self.uerr: dict | None = None  # FL1 — `consult.understand.error`
        self.in_bytes: dict = {"system": None, "message": None}
        self.obs: list[dict] = []
        self.sf: dict = {"sites": [], "carried": [], "from": None, "pair_kept": [], "pair_dropped": []}
        self.site_index_rec: dict | None = None
        self.material_ms: float | None = None
        self.understand_ms: float | None = None

    @classmethod
    def begin(cls, ctx: Ctx, conv: Conv, n: int, question: str, d: Deps, viewing: str | None = None) -> Consult:
        return cls(ctx, conv, n, question, d, viewing)

    async def understand(self, sink: Callable[[dict], None]) -> Walk:
        """① ~ ⑤(나) — 실패(FL1)면 객체 diff `[]` · 조회 0 · 「입구 없음」 의 빈 걷기."""
        t0 = time.perf_counter()
        s, pick = self.s, self.prompt.pick
        sink({"event": "step", "stage": "front"})  # UC10 — `understand`
        try:
            self.el = await el_mod.load(self.d.hub, aliases=s.chat_pick_aliases == "on",
                                        cache=s.chat_pick_list_cache == "on", gold_hash=self.conv.gold_hash)
        except el_mod.EntryListError as e:
            self.uerr = {"kind": "list", "detail": str(e)[:200]}
        if self.uerr is None:
            system = und.system_text(pick, self.el)
            msg = und.turn_message(self.conv.turns, self.obj, self.question, pick, s, self.el)
            self.in_bytes = {"system": len(system.encode("utf-8")), "message": len(msg.encode("utf-8"))}
            self.raw = await und.call(s, system, msg)
            self.uerr = self.raw.error
        if self.uerr is None:
            try:
                self.parsed = vf_mod.parse(self.raw.output_raw)
            except vf_mod.UnderstandJSONError as e:
                self.uerr = {"kind": "json", "detail": str(e)}
        self.understand_ms = _ms(t0)
        if self.uerr is not None:  # FL2 — 대상 없이 되묻는다(「입구 없음」 · 새 문구 0)
            w = Walk()
            w.end_state, w.reason = es.NO_ENTRY, None
            return w
        self.acc = acc = vf_mod.verify(self.parsed, self.el, self.obj, first_turn=self.prev_row is None,
                                       talk_streak=tk.talk_streak(self.conv.turns), talk_max=s.chat_talk_max,
                                       prev_row=self.prev_row)
        self.kind = acc.kind
        self.turn_kind = {"kind": acc.kind, "from": acc.kind_from}
        self.follow = acc.follow_target
        self._apply()
        if acc.kind == "talk":
            return Walk()
        sink({"event": "step", "stage": "hub"})  # `search`
        t1 = time.perf_counter()
        w = await mr_mod.build_walk(self.d, acc, self.sf["sites"], self.el)
        self.material_ms = _ms(t1)
        return w

    def _apply(self) -> None:
        """④ — 관찰(OU1 ~ OU4) · 거둠(OU8) · 바람(OU11) → 객체 · `focus`(OU9) · 누르기(FD6) · 부위 거르개(VF8 · OU7 · QS3)."""
        acc = self.acc
        assert acc is not None
        t0 = time.perf_counter()
        last = self.obj["asked"][-1] if self.obj["asked"] else None
        prev_ask = last if last and last.get("turn") == self.n - 1 else None
        subjects_before = {cst.node(x) for x in self.obj["subjects"]}
        self.obs = cst.observe_accepted(acc, prev_ask, self.obj) if acc.kind != "talk" else []
        wishes = [k for k in acc.kinds if k in cst.WISH_KINDS] if acc.kind != "talk" else []
        self.diff = cst.update(self.obj, self.obs, wishes, self.n, set(acc.withdraw))
        self.prev_ask = cst.mark_answered(self.obj, self.n, self.diff, subjects_before)
        self.ms["update_ms"] = _ms(t0)
        self._viewing()
        self.changed = any(x["op"] in ("add", "state", "withdraw", "focus") for x in self.diff)
        self.sf = sf_mod.decide(acc, self.obj, self.prev_row, self.obj.get("focus"))

    def build(self, w) -> dict | None:
        return w.build() or ({"gold_hash": self.el.gold_hash} if self.el is not None and self.el.gold_hash else None)

    async def after_talk(self) -> None:
        """TT2 · OU10 — 객체는 거둠으로만 바뀐다 · 바뀌었을 때만 객체 기준 조회 · 리스트."""
        await self._after(terminal=not self.changed or self.uerr is not None, reads=[], talk=True)

    async def after_walk(self, w) -> None:
        if self.conv.gold_hash and (self.build(w) or {}).get("gold_hash") not in (None, self.conv.gold_hash):
            self.skipped = "build_changed"
            return
        await self._after(terminal=w.end_state in NO_B_ON, reads=w.reads, talk=False, w=w)

    async def _site_options(self, site_code: str) -> list[dict]:
        """QS6 ⓐ — 그 부위에 `concern_site` 로 묶인 고민(색인 — `gold_hash` 마다 한 번)."""
        codes, rec = await si_mod.concerns_of_site(self.d.hub, self.el, site_code)
        if rec is not None:
            self.site_index_rec = rec
        return [{"table": "concern", "code": c, "name": self.el.name("concern", c), "layer": "", "holder": "",
                 "rank": -1, "first": i, "gids": [], "rows": 0} for i, c in enumerate(codes)]

    async def _after(self, *, terminal: bool, reads, talk: bool, w=None) -> None:
        reader = plan_mod.Reader(self.d.hub, self.conv.memo, reads, self.queries)
        t1 = time.perf_counter()
        try:
            if terminal:  # 그 턴에 필요해진 조회는 다음 턴이 따라잡는다(W-6) · 실패 턴은 조회 0(FL2)
                plan_mod.derive(self.obj, self.conv.memo, extra=list(reads or []))
            else:
                names = [self.el.name("site", c) for c in self.sf["sites"]] if self.el is not None else []
                calls = plan_mod.list_calls(self.obj, names, self.focus0, self.conv.list_calls)
                self.plan = await plan_mod.run(self.obj, reader, calls=calls,
                                               site_options=self._site_options if self.el is not None else None)
                cnames = {x["code"]: x["name"] for x in self.obj["concerns"]}
                base = build_list(self.plan.lists, cnames, codes=True) if self.plan.lists else None
                procs, mods = list_codes(base)
                self.syn = await syn_mod.run(self.obj, reader, self.n, procs, mods, on=self.s.chat_synergy == "on")
                self.diff += self.syn["diff"]
                self.list_state = state_list(self.obj, self.plan.lists, cnames, self.syn["bundles"],
                                             syn_mod.marks(self.syn["bundles"], self.obj))
                self._site()
        except Exception as e:  # noqa: BLE001 — 객체 조회가 깨져도 답은 낸다(기록만)
            self.error = {"kind": type(e).__name__, "detail": str(e)[:200]}
        self.ms["plan_ms"] = _ms(t1)
        new_nodes = {f"{x['table']}:{x['code']}" for x in self.diff if x["op"] == "add" and x["table"]}
        new_nodes |= {f"concern:{x['to']}" for x in self.diff if x["op"] == "focus" and x["to"]}
        new_guess = {f"{x['table']}:{x['code']}" for x in self.diff if x["op"] == "add" and x["to"] == "guess"}
        list_shown = bool(self.list_state and self.list_state["concerns"])
        self.blanks = blanks_mod.count(self.obj, self.plan, list_shown)  # L7
        kinds_read = list(w.front.kinds) if w is not None and w.front else []
        acc = self.acc
        axis_tg = [x for x in (acc.targets if acc else []) if x.get("sure")]  # 검수 L-5 — 축 항목 기준(guess 빼고 · RV1)
        qy7_new = bool(acc and acc.kind == "new" and axis_tg and all(x["table"] == "site" for x in axis_tg)
                       and not self.obj.get("focus") and any(k.startswith("site:") for k in self.plan.options))  # BQ4
        self.ask, self.ask_why = blanks_mod.pick(
            self.blanks, self.obj, self.n, new_nodes=new_nodes, new_guess=new_guess, prev_ask=self.prev_ask,
            end_state=w.end_state if w is not None else (es.NO_ENTRY if self.uerr is not None else None),
            kinds_read=kinds_read, ask_max=self.s.chat_ask_max, qy7_new=qy7_new)
        self._close(w, kinds_read, talk)

    def _viewing(self) -> None:
        """FD6 — 누른 고민(폼 `viewing` · 코드 여럿이면 객체에서 가장 최근 고민)으로 `focus` 를 옮긴다. 같은 턴 글의 새 고민이
        이긴다(W-2). 받는 코드 = 객체 축 고민 · 그 하위 고민(`concern_sites[]` 의 `하위` — W-1) · 하위 고민은 `how` = `view` 로
        객체에 든다(B-2). 장부 `consult.viewing{concern_code, accepted, rejected}`."""
        raw = [c for c in (self.viewing or "").split(",") if c.strip()]
        if not raw:
            return
        codes = [c.strip() for c in raw]
        axis = {x["code"]: x for x in cst.axis_items(self.obj, "concerns")}
        subs = viewable_subs(self.obj)
        ok = [c for c in codes if c in axis or c in subs]
        if not ok:
            self.viewing_rec = {"concern_code": ",".join(codes), "accepted": False, "rejected": "not_in_object"}
            return
        if any(x["op"] == "add" and x["slot"] == "concerns" and x["to"] in cst.AXIS and x["why"] == "text"
               for x in self.diff):  # W-2 — 같은 턴 글에서 새 고민이 들면 글이 이긴다
            self.viewing_rec = {"concern_code": ok[0], "accepted": False, "rejected": "text"}
            return
        in_obj = [c for c in ok if c in axis]
        pick = (max(in_obj, key=lambda c: (axis[c]["source"].get("turn") or 0, -in_obj.index(c))) if in_obj
                else ok[0])  # FD6 — `source.turn` 이 가장 큰 것
        if pick not in axis:  # B-2 — 하위 고민을 객체에 들인다(손님이 말한 것이 아니다 — how = view)
            it = {"table": "concern", "code": pick, "name": subs[pick] or plan_mod.concern_name_in(self.conv.memo, pick),
                  "state": "said",
                  "source": {"turn": self.n, "how": "view", "from_ask": None, "sure": None},  # X-28 — 손님 말이 아니다
                  "history": [{"turn": self.n, "state": "said"}]}
            self.obj["concerns"].append(it)
            self.diff.append({"op": "add", "slot": "concerns", "table": "concern", "code": pick, "from": None,
                              "to": "said", "why": "view"})
        old = self.obj.get("focus")
        if old != pick:
            self.obj["focus"] = pick
            self.diff.append({"op": "focus", "slot": "focus", "table": "concern", "code": pick, "from": old,
                              "to": pick, "why": "view"})
        self.viewing_rec = {"concern_code": pick, "accepted": True, "rejected": None,
                            "how": "view" if any(x["op"] == "add" and x["why"] == "view" for x in self.diff) else None}

    def _site(self) -> None:
        """SP-5 — 부위 거르개가 걸린 `focus` 리스트의 `site` → 장부 `list.site{name, …}` · 「앱 판정」 `site`."""
        names = [c.request["site"] for c in self.plan.list_calls if c.request.get("site")]
        self.site_view = None
        for c in (self.list_state or {}).get("concerns") or []:
            if c.get("site") and names:
                c["site"]["name"] = ", ".join(names)
                self.site_view = {"name": c["site"]["name"], "applied": c["site"]["applied"],
                                  "prefer_n": c["site"]["prefer_rows"]}

    def _close(self, w, kinds_read: list[str], talk: bool) -> None:
        """L7 — `empty[]`(EM1 · EM5 · EM6) → `close`(CS2). `talk` 턴은 새로 읽은 것이 없어 `empty` 0 · `offered` 0."""
        emp: list[dict] = []
        alts: list[dict] = []
        offered: list[str] = []
        if not talk and w is not None:
            f = w.front
            names = {e.node: e.name for e in (f.entries if f else [])}
            emp = close_mod.merge(close_mod.empties(w, names), close_mod.synergy_empty(self.obj, self.queries, kinds_read),
                                  close_mod.list_empty(self.list_state if w.end_state not in NO_B_ON else None))
            if emp:
                alts = close_mod.alt(self.list_state, self.obj.get("focus"), self._target_mods(w),
                                     self.s.chat_alt_max)
            if f is not None and w.end_state not in NO_B_ON:
                offered = [o["code"] for o in f.offered if o.get("code")]
        self.empty = emp
        self.empty_view = close_mod.view(emp, self.prompt.kind_labels)
        self.close = close_mod.pick(self.ask, emp, alts, offered)

    def _target_mods(self, w) -> set[str]:
        """EM3 — 이번 대상의 계열(계열 대상 · 대상 profile 의 계열 · 리스트에서 대상 시술이 든 계열)."""
        f = w.front
        nodes = {e.node for e in (f.entries if f else [])}
        mods = {e.code for e in (f.entries if f else []) if e.table == "modality"}
        for node, rows in mat._profiles(w.reads).items():
            if node in nodes:
                mods |= {str(x.get("modality_code")) for x in rows if x.get("modality_code")}
        procs = {e.code for e in (f.entries if f else []) if e.table == "procedure"}
        for c in (self.list_state or {}).get("concerns") or []:
            for m in c.get("modalities") or []:
                if m.get("modality_code") and any(p["procedure_code"] in procs for p in m.get("procedures") or []):
                    mods.add(m["modality_code"])
        return mods

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
                    rows.append((key, gids, {"option": True, "concern_name": o["name"],
                                             "holder": r.get("holder") or o.get("holder") or ""}))  # 0.3 VO4 · EM4
                rule = "QY3" if self.ask["bk"] == "BK2" else "QY7"
                ns = mat.add_summaries(tm, rule, {"about": self._name(self.ask["about"])}, rows,
                                       self.conv.summaries)
                opt_n = {o["code"]: k for o, k in zip(self.ask["options"], ns, strict=True)}
            raw_opts = {x["code"]: x for x in self.plan.options.get(self.ask["about"], [])}
            self.ask_view = {"slot": self.ask["slot"], "about": self._name(self.ask["about"]),
                             "options": [{"name": o["name"], "holder": o.get("holder") or (raw_opts.get(o["code"]) or {}).get("holder") or "",
                                          **({"n": opt_n[o["code"]]} if o["code"] in opt_n else {})}
                                         for o in self.ask["options"]]}  # 0.3 VO4 — `layer` 대신 주인 이름
        rows = []
        for ln in self.syn["lines"]:
            p = ln["partner"]
            rows.append((syn_mod.line_key(ln["a"], p), p["gids"],
                         {"synergy": True, "with": ln["a_name"], "name": p["name"], "table": p["table"],
                          "a_layer": p["a_layer"], "same_day": p["same_day"],
                          "goal_effects": p["goal_effects"], "same_day_caution": p["caution"]}))
        if rows:
            self.syn_lines = mat.add_summaries(tm, "QY4", {}, rows, self.conv.summaries)

    def _name(self, about: str) -> str:
        table, _, code = about.partition(":")
        it = cst.find(self.obj, table, code)
        return (it or {}).get("name") or ""

    def message(self, w, tm: mat.TurnMaterial) -> str:
        talk = self.kind == "talk"
        if talk or w.end_state not in NO_B_ON:
            self._summaries(tm)
        self.tm = tm
        if talk:  # TT3 — 「앱 판정」 = turn_kind · 앞 턴 블록 · 끝맺음 · seen_lines [] · 새 재료 줄 0(요약 줄은 객체가 바뀐 때만)
            self.prev_view = tk.prev_block(self.prev_row, self.prompt.kind_labels)
            j = {"turn_kind": "talk", "seen_lines": [], "ask": self.ask_view, "synergy_lines": self.syn_lines,
                 "empty": self.empty_view}
        else:
            extra = {"turn_kind": self.turn_kind["kind"], "ask": self.ask_view, "synergy_lines": self.syn_lines,
                     "empty": self.empty_view}  # AN1 — `turn_kind` = 검증 뒤 `kind`
            if self.site_view is not None:
                extra["site"] = self.site_view
            j = mat.judgement_v3(w, tm, self.n, kind_labels=self.prompt.kind_labels, extra=extra)
        self.close_view = close_mod.close_view(self.close, self.ask_view, self.prompt.kind_labels)
        return mat.message_v3(self.prompt, j, self.close_view, self.prev_view, tm, self.question,
                              mat.consult_view(self.obj))

    def check_voice(self, body: str) -> None:
        """0.3 VO10 — 기록만(답을 고치지 않는다)."""
        if self.s.chat_voice_check == "on":
            self.voice = voice_mod.check(body, self.prompt.voice_check)

    def drop_extras(self) -> None:
        self.ask, self.ask_why, self.ask_view, self.syn_lines = None, "FT5", None, []
        self.empty, self.empty_view = [], []
        self.close = close_mod.pick(None, [], [], [])
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
        sites = place(site_pairs(self.obj), self.obj.get("focus"))  # 0.3 FD1 · FD2 — 대화의 고민 전부(누적)
        last = self.conv.last_sites()
        focus_moved = any(x["op"] == "focus" for x in self.diff)  # FD3 — `focus` 가 바뀐 턴은 빈 조각도 보낸다(검수 W-1)
        if focus_moved or (sites != last and not (last is None and not sites["dots"] and not sites["labels"])):
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

    def understand_view(self) -> dict:
        """장부 `consult.understand`(§6-1) — 턴 메시지 전문은 싣지 않고 바이트 수만."""
        raw, acc, pick = self.raw, self.acc, self.prompt.pick or {}
        return {"model": self.s.chat_pick_model, "thinking": self.s.chat_pick_thinking,
                "pick_version": pick.get("version"), "list_sha": self.el.sha if self.el else None,
                "aliases": self.s.chat_pick_aliases == "on", "input_bytes": self.in_bytes,
                "output_raw": raw.output_raw if raw else None,
                "parsed": vf_mod.parsed_view(self.parsed) if self.parsed is not None else None,
                "accepted": acc.view() if acc else None, "dropped": acc.dropped if acc else [],
                "merged": acc.merged if acc else [], "added_from_sites": acc.added_from_sites if acc else [],
                "ms": raw.ms if raw else self.understand_ms, "api_ms": raw.api_ms if raw else None,
                "usage": {k: (raw.usage or {}).get(k) for k in ("input_tokens", "cache_read_input_tokens",
                                                                "output_tokens", "thinking_tokens")} if raw else None,
                "error": self.uerr}

    def targets(self) -> list[dict]:
        """턴 줄 `targets[]` = `{table, code, name, sure, how}`(§6-1 — `by` · `noul` 없음)."""
        how = {(o["table"], o["code"]): o["how"] for o in self.obs}
        return [{"table": t["table"], "code": t["code"], "name": t.get("name") or "", "sure": bool(t.get("sure")),
                 "how": how.get((t["table"], t["code"]), "text")} for t in (self.acc.targets if self.acc else [])]

    def ledger(self, w, err: dict | None, conv_end: str | None) -> tuple[list[dict], dict]:
        if self.skipped:
            return [], {"skipped": self.skipped, "diff": [], "after": None, "understand": self.understand_view(),
                        "turn_kind": self.turn_kind}
        if err is None:
            self._info(w)
            if self.ask is not None and not (self.close.get("by") == "ask" and self.close.get("said") == 0):  # W-8
                blanks_mod.record(self.obj, self.ask, self.n)
                self.diff.append({"op": "asked", "slot": "asked", "table": None, "code": self.ask["slot"], "from": None,
                                  "to": self.ask["about"], "why": self.ask_why})
            if self.plan.list_calls:
                self.conv.list_calls = list(self.plan.list_calls)  # QS4 — 다음 턴의 「앞 리스트」(메모리만)
        out = {"diff": self.diff, "after": self.obj if err is None else None,
               "blanks": blanks_mod.ledger_view(self.blanks),
               "ask": ({k: self.ask[k] for k in ("slot", "about", "options", "why")} if self.ask else None),
               "ask_why": self.ask_why, "queries": self.queries,
               "synergy_lines": self.syn_lines, "turn_kind": self.turn_kind, "follow_target": self.follow,
               "close": self.close, "viewing": self.viewing_rec,
               "empty": self.empty, **({"voice": self.voice} if self.voice is not None else {}),
               "understand": self.understand_view(), "site_filter": self.sf,
               **({"site_index": self.site_index_rec} if self.site_index_rec is not None else {}),
               "hub_queries": len(self.queries) - sum(1 for q in self.queries if q.get("skipped")), **self.ms}
        if self.error:
            out["error"] = self.error
        if err is not None:
            out["not_counted"] = True
        return [], out
