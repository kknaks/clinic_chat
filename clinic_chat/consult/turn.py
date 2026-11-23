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
from clinic_chat.consult import ask_axis as ax_mod
from clinic_chat.consult import blanks as blanks_mod
from clinic_chat.consult import carry as carry_mod
from clinic_chat.consult import cart as cart_mod
from clinic_chat.consult import close as close_mod
from clinic_chat.consult import detect as dt
from clinic_chat.consult import entry_list as el_mod
from clinic_chat.consult import fetch_read as fr_mod
from clinic_chat.consult import images as img_mod
from clinic_chat.consult import items_view as iv_mod
from clinic_chat.consult import kos_block as kb_mod
from clinic_chat.consult import material as mat
from clinic_chat.consult import material_read as mr_mod
from clinic_chat.consult import modal as modal_mod
from clinic_chat.consult import plan as plan_mod
from clinic_chat.consult import site_filter as sf_mod
from clinic_chat.consult import site_index as si_mod
from clinic_chat.consult import state as cst
from clinic_chat.consult import synergy as syn_mod
from clinic_chat.consult import topics as tp
from clinic_chat.consult import turn_kind as tk
from clinic_chat.consult import understand as und
from clinic_chat.consult import verify as vf_mod
from clinic_chat.consult import voice as voice_mod
from clinic_chat.consult.kos_map import KosMap
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
    kos: KosMap | None = None  # 0.5 — 매핑 JSON v3 색인(서버 시작 때 한 번 · JS1)


def fig_re(marker: str):
    """CH3 — 프롬프트 `kos.fig_marker`(예 `[[…:{code}]]`)의 줄 꼴 정규식(코드 = MEC-000)."""
    import re

    pre, _, post = marker.partition("{code}")
    return re.compile(r"^[ \t]*" + re.escape(pre) + r"(MEC-\d{3})" + re.escape(post) + r"[ \t]*$", re.M)


_COUNTRY: dict[str, dict[str, str]] = {}  # 0.5 CD6 — gold_hash → 제품 국가(프로세스 캐시)


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


TOKEN_KEYS = (("input", "input_tokens"), ("output", "output_tokens"), ("cache_creation", "cache_creation_input_tokens"),
              ("cache_read", "cache_read_input_tokens"))


def _int_or_none(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def call_tokens(cs, llm_res: dict | None) -> dict:
    """11-23 「호출마다 토큰 기록」 — 장부 `tokens`: 옛 칸(답 호출 usage 평평한 꼴 — `token-cause` · `fetch-plan` 이 읽음)은
    그대로 두고 `calls[]`(상담 경로 LLM 호출마다 `{role, model, ms, input, output, cache_creation, cache_read, thinking}`) ·
    `sum`(턴 합 · 네 칸)을 더한다. 값이 없으면 null(지어 넣지 않음) · CLI 왕복이 여럿이면 `llm/cli.py` 가 왕복 합을 낸 값."""
    base = dict((llm_res or {}).get("tokens") or {})
    calls: list[dict] = []
    raw = getattr(cs, "raw", None) if cs is not None else None
    if raw is not None:  # ② 이해 호출(`understand.call`)
        u = raw.usage or {}
        calls.append({"role": "understand", "model": ",".join(raw.models) or None, "ms": raw.ms,
                      **{k: _int_or_none(u.get(src)) for k, src in TOKEN_KEYS},
                      "thinking": _int_or_none(u.get("thinking_tokens"))})
    if llm_res is not None:  # ⑧ 답 호출(`ChatLLM.turn` — `--resume`)
        calls.append({"role": "answer", "model": llm_res.get("observed_model"),
                      "ms": ((llm_res.get("timing") or {}).get("total_ms")),
                      **{k: _int_or_none(base.get(src)) for k, src in TOKEN_KEYS}, "thinking": None})
    tot: dict[str, int | None] = {}
    for k, _ in TOKEN_KEYS:
        vals = [c[k] for c in calls if c[k] is not None]
        tot[k] = sum(vals) if vals else None
    return {**base, "calls": calls, "sum": tot}


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
    size_rec: dict | None = None
    tm = mat.TurnMaterial(next_n=conv.next_n)
    lst = sites = None
    msg = ""
    if conv.gold_hash and gh and gh != conv.gold_hash:  # CL14 · C-20 — 앞 턴 재료의 줄이 다른 판의 것
        err, conv_end = {"kind": "build_changed", "stage": "walk", "detail": None}, "build_changed"
    else:
        if w.end_state not in NO_B_ON and not talk and cs is None:
            tm = mat.renumber(w, conv.sent, conv.summaries, conv.next_n, v3=False)
        if talk:  # TT1 ~ TT3 — 걷기 없이 객체 갱신 · 바뀌었을 때만 객체 기준 조회 · 새 재료 줄 0
            await cs.after_talk()
            msg = cs.message(w, tm)
        elif cs is not None:  # L4 · L5 · L7 — 객체 갱신 · 객체 기준 조회 · 시너지 · 빈 칸(메시지보다 먼저)
            await cs.after_walk(w)
            if w.end_state not in NO_B_ON:  # SPEC-011 — 2차 읽기(G6 은 이번 턴 주제를 본다 → 감지 뒤)
                cut = cs.fetch_cut(w)
                on = s.chat_fetch_cut == "on"
                tm = mat.renumber(w, conv.sent, conv.summaries, conv.next_n, v3=True, cut=cut if on else None,
                                  sent_names=conv.sent_names)
                if not on:  # 끈 상태 — 판단만 기록(재생에서 인용 줄과 맞대 빠짐을 센다)
                    for ln in tm.new_lines:
                        if ln.get("gid") in cut:
                            ln["fetch_would"] = ":".join(cut[ln["gid"]])
            msg = cs.message(w, tm)
        else:
            msg = mat.message(prompt, mat.judgement_of(w, tm, n), tm, question)
        sys_t = cs.system_text if cs else None
        size_rec = {**mat.size_of(prompt, msg, sys_t), "limit": s.answer_input_max_tokens}  # 테스트 고침 E — 원인 재기
        if cs is not None and cs.fetch_plan is not None:  # SPEC-011 LG3
            size_rec["by_fetch"] = cs.fetch_size(tm)
        if mat.too_large(prompt, msg, s.answer_input_max_tokens, sys_t) and cs is not None and not tm.empty:
            # 테스트 고침 E(11-23) — 넘으면 재료를 줄여서라도 답한다(중단 0) · 어떻게 줄였는지 장부 `material_size.shrink`

            def fits(t: mat.TurnMaterial) -> bool:
                return not mat.too_large(prompt, cs.message(w, t), s.answer_input_max_tokens, sys_t)

            size_rec["shrink"] = mat.shrink(tm, fits)
            msg = cs.message(w, tm)
            size_rec["after"] = mat.size_of(prompt, msg, sys_t)
        if mat.too_large(prompt, msg, s.answer_input_max_tokens, sys_t):  # AP8 · C-21(줄여도 못 실으면 옛 길)
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
            if cs.items_view is not None:  # 0.5 §4-9 — 목록 + 점 · 몸 목록(LLM 답보다 먼저)
                push("items", r.items(cs.items_view, conv.conv_id))
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
        fig, fig_dropped = cs.figure(body) if cs is not None else (None, [])
        push("done", r.answer(n, body, w.end_state, fig))
    timing["total_ms"] = _ms(t0)
    hub_ms = round(sum(x.get("ms") or 0 for x in d.hub.log[hlog0:]), 1)
    llm_ms = ((llm_res or {}).get("timing") or {}).get("total_ms")
    timing["parts"] = ({"understand_ms": cs.understand_ms, "hub_ms": hub_ms, "llm_ms": llm_ms,
                        "material_ms": cs.material_ms} if cs is not None else
                       {"jev_ms": round(getattr(d.jev, "ms_total", 0.0) - jms0, 1), "hub_ms": hub_ms, "llm_ms": llm_ms,
                        "walk_ms": walk_ms})
    llm = dict((llm_res or {}).get("llm") or {})
    if cs is not None and cs.kos_meta is not None:  # 0.5 LC6
        llm["kos_block"] = cs.kos_meta
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
        "timing": timing, "tokens": call_tokens(cs, llm_res),
        "thinking_seen": (llm_res or {}).get("thinking_seen"), "build": build,
        "prompts": {"version": prompt.version, "consult_at_start": conv.prompt_sha, "consult_now": now_sha,
                    "changed": bool(now_sha and conv.prompt_sha and now_sha != conv.prompt_sha)},
        "commits": {"app": app_commit()}, "error": err, "conv_end": conv_end,
        "material_size": size_rec,  # 테스트 고침 E — 답 입력 크기 · 줄임 기록
    })
    if consult_row is not None:  # L9 — 장부 `consult{…}`(켠 턴의 `carried[]` = [] — AR-9)
        row["carried"], row["consult"] = consult_row
    if cs is not None and cs.items_rec is not None:  # 0.5 §5-1 ⑦ — 장부 `items{…}`
        row["items"] = cs.items_rec
    if cs is not None and llm_res is not None and not llm_err:  # 0.5 CH3 — 채팅 원리 그림(다시 읽기에도)
        f, dropped = cs.figure(body)
        row["fig"] = {"fig": f, "dropped": dropped}
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
        self.topics0, self.active0 = conv.last_topics(view=False)  # 11-23 — 턴 판단은 머리 누름(보기) 빼고 마지막 턴 주제
        self.dec: tp.Decision | None = None
        self.pairs_rec: dict | None = None  # 테스트 고침 E — 짝 판단 기록
        self.items_view: dict | None = None  # 0.5 `items` 이벤트 값(바뀐 턴만)
        self.items_rec: dict | None = None  # 0.5 장부 `items{…}`
        self.detect_error: dict | None = None
        self.axis: dict | None = None  # 0.5 §5-4 — 장부 `consult.ask_axis{axis, options, scores, …}`
        self.kos_text: str | None = None  # 0.5 §7-1 — 턴 메시지 블록
        self.kos_meta: dict | None = None  # 장부 `llm.kos_block`
        self.figs: list[dict] = []  # CH3 — 이번 턴 받을 수 있는 원리 그림(코드 · 이름 · 파일 있음)
        self.fetch_plan: fr_mod.Plan | None = None  # SPEC-011 LG2 — 2차가 실제로 쓴 값
        self.fetch_decided: dict[str, tuple[str, str]] = {}
        self.fetch_g2: list[str] = []
        if self.prompt.kos:  # 0.5 §7-2 — 답 규칙 R1 ~ R11(초안)을 켠 상태 시스템 글 끝에
            self.system_text = self.system_text + "\n\n" + "\n".join(self.prompt.kos["rules"])

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
            msg = und.turn_message(self.conv.turns, self.obj, self.question, pick, s, self.el,
                                   topics=self._topic_brief())
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
                                       prev_row=self.prev_row, prev_topic_ids=self._prev_topic_ids())
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

    def _cur_topic(self) -> dict | None:
        return next((t for t in self.topics0 if str(t.get("id")) == str(self.active0)), None) or (
            self.topics0[-1] if self.topics0 else None)

    def _prev_topic_ids(self) -> list[str]:
        cur = self._cur_topic()
        return [str(t["id"]) for t in tp.visible_prev(self.topics0, cur, self.s.chat_topic_max)]  # 검수 P2-1

    def _topic_brief(self) -> dict:
        """TP13 — 이해 호출 턴 메시지의 주제 블록(지금 주제 id + 앞 주제 · 머리 = IL11 과 같은 조건 이름 · 수)."""
        cur = self._cur_topic()

        def one(t: dict) -> dict:
            return {"id": str(t["id"]), "conds": iv_mod.cond_names(t.get("conditions")), "n": len(t.get("items") or []),
                    "slots": iv_mod.cond_tuple(t.get("conditions"))}

        prev = tp.visible_prev(self.topics0, cur, self.s.chat_topic_max)  # 검수 P2-1 — 한도 안 앞 주제만
        return {"current": one(cur) if cur else None, "prev": [one(t) for t in prev]}

    async def _detect(self) -> None:
        """0.5 §5-1 ② ~ ⑦ — 이름 일치 · 이번 턴 조건 · 주제 고르기(TP2 · TP3 · TP14) · 칸 → 진료항목 · LS · 장부 · 화면 값."""
        kos, acc = self.ctx.kos, self.acc
        if kos is None or acc is None or self.uerr is not None:
            return
        t0 = time.perf_counter()
        talk = acc.kind == "talk"
        hits = [] if talk else dt.name_hits(kos, self.question)
        turn, order = tp.turn_conditions([] if talk else acc.targets, hits, None if talk else acc.country,
                                         follow=acc.follow_target, exclude=list(acc.exclude or []))
        gh = (self.el.gold_hash if self.el is not None else "") or ""
        det = dt.Detector(kos, self.d.hub, cache={}, countries=_COUNTRY.setdefault(gh, {}))
        await self._product_procedures(kos, det, turn)

        async def count(conds: dict) -> list[str]:
            return (await det.items(conds)).codes

        subs, self.pairs_rec = (None, None) if talk else await self._split_subs(kos, det, acc)
        if subs:  # 테스트 고침 E — 부위마다 다른 시술 · 제품 짝 → 주제를 찢는다
            self.dec = dec = await tp.split_decide(self.topics0, self.active0, subs, n=self.n, count=count,
                                                   llm=acc.topic)
            dec.rec["pairs"] = self.pairs_rec
        else:
            self.dec = dec = await tp.decide(self.topics0, self.active0, turn, order, n=self.n,
                                             utterance=self.question, llm=None if talk else acc.topic,
                                             withdrawn=list(acc.withdraw), count=count, max_n=self.s.chat_topic_max,
                                             info=tp.info_question(acc.kinds, acc.out, self.question))
            if self.pairs_rec:
                dec.rec["pairs"] = self.pairs_rec
        if det.errors:  # 검수 P1-1 — 허브 오류는 「0 결과」 가 아니다: 이 턴은 주제 · 조건 · 목록을 바꾸지 않는다
            cur0 = self._cur_topic()
            self.dec = tp.Decision(self.topics0, self.active0, {**dec.rec, "change": False, "hub_error": True})
            codes0 = list((cur0 or {}).get("items") or [])
            self.items_rec = {"codes": codes0, "conditions": (cur0 or {}).get("conditions"), "turn_conditions": turn,
                              "hub_error": True, "errors": list(det.errors), "name_hits": [h.view() for h in hits],
                              "ms": _ms(t0), "hub_calls": det.hub_calls}
            return
        cur = next((t for t in dec.topics if str(t["id"]) == str(dec.active)), None)
        res = await det.items(cur["conditions"]) if cur and not tp.is_empty(cur["conditions"]) else dt.Result()
        codes = list((cur or {}).get("items") or [])
        added_sites = self._add_sites(kos, det, cur, turn)
        self.items_rec = {"codes": codes, "by": {c: res.by.get(c, []) for c in codes},
                          "conditions": (cur or {}).get("conditions"), "turn_conditions": turn,
                          "blocked": dec.blocked, "ls": dec.ls, "name_hits": [h.view() for h in hits],
                          "dropped_reach": res.dropped_reach, "upper": res.upper, "errors": res.errors,
                          "country_dropped": res.country_dropped if (cur or {}).get("conditions", {}).get("country") else None,
                          "case_scope": res.case_scope, "sites_added": added_sites, "excluded": res.excluded,
                          "pick_zero": bool(dec.rec.get("pick_zero")),
                          "ask_topics": dec.ask,
                          "ms": _ms(t0), "hub_calls": det.hub_calls}
        if (dec.topics, dec.active) != (self.topics0, self.active0) or dec.changed:
            self.items_view = iv_mod.topics_view(kos, dec.topics, dec.active, max_n=self.s.chat_topic_max)
        await self._axis(kos, gh, cur, codes)

    async def _split_subs(self, kos, det, acc) -> tuple[list[dict] | None, dict | None]:
        """테스트 고침 E(11-23) — 부위 → 시술 · 제품 짝. 규칙(한 문장 안 차례 — `tp.pair_groups`)이 찢기면 규칙, 아니고 이해 호출
        `pairs` 가 찢기면 이해 호출 짝(같은 꼴로 묶어 같은 판정 `tp.should_split`). 고민은 그 부위 고민만 따라간다 — 허브
        `concern_candidates` 응답 머리의 고민 부위(「직접」)가 그 부위와 같거나 조상 · 자손이면 그 부위 고민 · 부위 없는 고민은
        모든 짝에. → (짝마다 {turn, site, keep_concern} | None, 장부 `topic.pairs{from, groups}` | None)."""
        rule = tp.pair_groups(acc.targets)
        by = {(t["table"], t["code"]): t for t in acc.targets}
        llm_groups: list[dict] = []
        for p in acc.pairs or []:
            st = by.get(("site", p["site"])) or {"table": "site", "code": p["site"], "name": ""}
            pk = by.get((p["pick"]["table"], p["pick"]["code"])) or p["pick"]
            g = next((x for x in llm_groups if x["site"]["code"] == p["site"]), None)
            if g is None:
                llm_groups.append({"site": st, "picks": [pk]})
            elif pk not in g["picks"]:
                g["picks"].append(pk)
        src, groups = (("rule", rule) if tp.should_split(rule)
                       else ("llm", llm_groups) if tp.should_split(llm_groups) else (None, None))
        rec = {"from": src, "rule": [[g["site"].get("name") if g["site"] else None, [p.get("name") for p in g["picks"]]]
                                     for g in rule],
               "llm": [[g["site"].get("name"), [p.get("name") for p in g["picks"]]] for g in llm_groups]}
        if groups is None:
            return None, (rec if acc.pairs else None)
        cur = self._cur_topic()
        concerns = [t for t in acc.targets if t["table"] == "concern"] + [
            v for v in ((cur or {}).get("conditions") or {}).get("concern") or []]
        csites: dict[str, list[str]] = {}
        for v in concerns:
            if v["code"] not in csites:
                try:
                    await det.concern(v["code"], dt.Result())
                except Exception:  # noqa: BLE001 — 고민 부위를 못 읽으면 그 고민은 부위 없는 고민(모든 짝)
                    pass
                csites[v["code"]] = det.concern_sites(v["code"])

        def goes(v: dict, site: str) -> bool:
            cs_ = csites.get(v["code"]) or []
            if not cs_:
                return True
            fam = {site, *kos.ancestors(site)}
            return any(c in fam or site in kos.ancestors(c) for c in cs_ if c in kos.sites)

        subs = []
        for g in groups:
            if g["site"] is None or not g["picks"]:
                continue
            sc = g["site"]["code"]
            tg = [g["site"], *g["picks"], *[t for t in acc.targets if t["table"] == "concern" and goes(t, sc)]]
            sub_turn, _ = tp.turn_conditions(tg, [], acc.country, exclude=list(acc.exclude or []))
            await self._product_procedures(kos, det, sub_turn)
            keep = [v for v in ((cur or {}).get("conditions") or {}).get("concern") or [] if goes(v, sc)]
            subs.append({"turn": sub_turn, "site": sc, "keep_concern": keep})
        return (subs if len(subs) >= 2 else None), rec

    async def _product_procedures(self, kos, det, turn: dict) -> None:
        """11-23 — 제품 = 시술 고르기: 이번 턴 제품 값마다 그 제품(하위 제품 포함 — 허브 `scope` 「하위 제품」 · 감지와 같은 길)을
        가진 `$J` 진료항목들의 허브 시술(`hub.procedure_codes`)을 시술 칸에도 넣는다(고르기 묶음 교체가 「써마지」 → 「보톡스」 를
        같은 주제 안 교체로 보게). 허브 오류면 그 제품만 건너뜀(시술 칸 0 — 제품 칸은 그대로)."""
        have = {(v.get("table"), v.get("code")) for v in turn.get("procedure") or []}
        for v in turn.get("product") or []:
            try:
                items = kos.sort_codes(await det.product(v.get("code") or ""))
            except Exception:  # noqa: BLE001 — 시술 채우기는 덤 · 감지가 같은 오류를 따로 셈
                items = list(kos.by_product.get(v.get("code") or "", ()))
            for c in items:
                for pc in kos.item[c]["hub"].get("procedure_codes") or []:
                    if ("procedure", pc) not in have:
                        have.add(("procedure", pc))
                        name = self.el.name("procedure", pc) if self.el is not None else ""
                        turn["procedure"].append({"table": "procedure", "code": pc, "name": name or "", "from": "product"})

    @staticmethod
    def _add_sites(kos, det, cur: dict | None, turn: dict) -> list[str]:
        """테스트 고침 A-1 · A-6(사용자 11-22) — 이번 턴 말한 부위 + 고민의 부위(concern_site 「직접」)를 지금 주제의 점으로
        쌓는다(주제 `sites` · 장부 `consult.topics` 에 같이 남아 되살림 때 같이). 지금 주제 조건에 든 값만(막힌 값은 점 아님)."""
        if cur is None:
            return []
        conds = cur.get("conditions") or {}
        in_site = {v.get("code") for v in conds.get("site") or []}
        in_concern = {v.get("code") for v in conds.get("concern") or []}
        said = [v["code"] for v in turn.get("site") or [] if v.get("code") in in_site]
        for v in turn.get("concern") or []:
            if v.get("code") in in_concern:
                said += det.concern_sites(v["code"])
        have = list(cur.get("sites") or [])
        new = [x for x in dict.fromkeys(said) if x in kos.sites and x not in have]
        cur["sites"] = have + new
        return new

    async def _axis(self, kos, gh: str, cur: dict | None, codes: list[str]) -> None:
        """0.5 §5-4 AX1 ~ AX6 — 지금 주제(TP9) 목록을 가장 잘 가르는 축 · 선택지(목록이 한도 이하면 축 0)."""
        conds = (cur or {}).get("conditions") or {}
        filled = {s for s in ax_mod.ITEM_AXES if conds.get(s)}
        try:
            if len(codes) > self.s.chat_ask_list_min:
                vals, names, skipped, calls = await ax_mod.axis_values(kos, self.d.hub, gh, codes)
            else:
                vals, names, skipped, calls = {}, {}, [], 0
            self.axis = ax_mod.pick(codes, vals, filled=filled, order=kos.order, names=names,
                                    min_list=self.s.chat_ask_list_min, skipped=skipped)
            self.axis["hub_calls"] = calls
        except Exception as e:  # noqa: BLE001 — 축이 깨지면 0.2 빈 칸 질문 그대로(기록만)
            self.axis = {"axis": None, "error": {"kind": type(e).__name__, "detail": str(e)[:200]}}

    async def _kos_block(self, reads) -> None:
        """0.5 §7-1 LC1 ~ LC9 · CH3 — 블록 글 · 장부 칸 · 원리 그림 후보(재료 · 본 진료항목의 원리 코드 가운데 파일 있는 것)."""
        kos = self.ctx.kos
        if kos is None or not self.prompt.kos:
            return
        conv, s = self.conv, self.s
        image_dir = str(s.path(s.chat_image_dir)) if s.chat_image_dir else None
        gold = kos.hub_gold_hash
        mechs: dict[str, str] = {}
        for r in reads or []:
            ra = getattr(r, "ra", None)
            body = ra.as_response() if ra is not None and hasattr(ra, "as_response") else {}
            for row in (body.get("rows") or []) if isinstance(body, dict) else []:
                if isinstance(row, dict) and row.get("mechanism_code"):
                    mechs.setdefault(row["mechanism_code"], row.get("mechanism_name") or "")
        course = []
        for c in list(conv.viewed):
            if c not in kos.item:
                continue
            pm = await modal_mod.build(kos, self.d.hub, gold, c, image_dir)
            for m in pm["mechanisms"]:
                if m["code"]:
                    mechs.setdefault(m["code"], m["name"])
            if pm["course"] or pm["side_other"]:
                course.append({"code": c, "name": pm["name"], "course": pm["course"], "side_other": pm["side_other"]})
        self.figs = [{"code": k, "name": v} for k, v in mechs.items() if img_mod.present(image_dir, "mechanism", k)]
        sent = kb_mod.sent_before(conv.turns)
        pairs: dict[tuple[str, str], list[dict]] = {}
        all_pairs = kb_mod.cart_pairs(kos, conv.cart)
        # WP-6 검수 S-2 — 같이 받기 표지 + 장바구니 진료항목 둘 이상이면 장바구니 · 쌍 줄을 이 턴에 다시(LC5 예외)
        force = len(conv.cart) >= 2 and any(w in (self.question or "") for w in kb_mod.TOGETHER)
        for a, b in all_pairs[: kb_mod.PAIRS_MAX]:
            if f"{a}|{b}" in sent["pairs"] and not force:
                continue
            ra = await self.d.hub.read_all("pair", {"a": f"procedure:{a}", "b": f"procedure:{b}"})
            pairs[(a, b)] = list(ra.items) if ra.complete else []
        dec = self.dec
        cur = self._cur_topic() if dec is None else next((t for t in dec.topics if str(t["id"]) == str(dec.active)), None)
        topics = dec.topics if dec is not None else self.topics0
        brief = [{"id": str(t["id"]), "slots": iv_mod.cond_tuple(t.get("conditions")), "n": len(t.get("items") or [])}
                 for t in tp.visible_prev(topics, cur, s.chat_topic_max)]  # 주제 튜플(11-22) — 칸 묶음 그대로
        cond = kb_mod.cond_view(dec, self.items_rec, self.axis, brief, items_max=s.chat_kos_llm_items_max)
        items = list((self.items_rec or {}).get("codes") or [])
        cart_rec = cart_mod.rec_view(kos, conv.cart, cards=s.chat_rec_max) if conv.cart else None
        self.kos_text, self.kos_meta = kb_mod.build(
            kos, self.prompt.kos, cond=cond, items=items, cart=conv.cart, cart_rec=cart_rec, viewed=list(conv.viewed),
            pairs=pairs, pairs_capped=len(all_pairs) > kb_mod.PAIRS_MAX, course=course, figs=self.figs, sent=sent,
            items_max=s.chat_kos_llm_items_max, force_cart=force)

    def fetch_cut(self, w) -> dict[str, tuple[str, str]]:
        """SPEC-011 §5 G1 ~ G6 — 1차 `fetch` 대로 이번 턴 걷기 줄마다 전체 · 이름만 · 뺌(「읽고 잘라 내기」 — 허브 호출은 그대로)."""
        acc = self.acc
        if acc is None or w.material is None or not w.material.lines:
            return {}
        plan = fr_mod.Plan.of(acc.fetch)
        plan.layers = "sub"  # JC3 — 2차는 `sub` 고정(측정 62/62 · 다른 값은 빠짐만)
        self.fetch_plan = plan
        f = w.front
        tables = [e.table for e in (f.entries if f else [])]
        dec = self.dec
        cur = (next((t for t in dec.topics if str(t["id"]) == str(dec.active)), None) if dec is not None
               else self._cur_topic())
        conds = (cur or {}).get("conditions") or {}
        tg = {t["code"] for t in acc.targets} | ({acc.follow_target["code"]} if acc.follow_target else set())
        ctx = fr_mod.Ctx(targets=tg, question=self.question or "", md=fr_mod.md_sets(self.ctx.kos),
                         g2=fr_mod.g2_kinds(tables, acc.kinds, plan),
                         topic_concerns={v.get("name") for v in conds.get("concern") or [] if v.get("name")},
                         topic_codes=fr_mod.topic_codes(self.ctx.kos, (cur or {}).get("items")))
        self.fetch_g2 = sorted(ctx.g2)  # G2 종류(장부 `consult.fetch.g2`)
        self.fetch_decided = fr_mod.decide(fr_mod.app_lines(w), plan, ctx)
        return self.fetch_decided

    def fetch_view(self) -> dict | None:
        """장부 `consult.fetch`(LG2) — 2차가 쓴 값 + G2 종류 + 줄 판단 수."""
        if self.fetch_plan is None:  # 2차를 안 탄 턴(talk · 재료 없음) — 1차 값만
            return {**self.acc.fetch, "applied": False} if self.acc is not None else None
        cnt: dict[str, int] = {}
        for fate, why in self.fetch_decided.values():
            cnt[f"{fate}:{why}"] = cnt.get(f"{fate}:{why}", 0) + 1
        return {**self.fetch_plan.view(), "g2": self.fetch_g2, "lines": cnt, "applied": True}

    def fetch_size(self, tm: mat.TurnMaterial) -> dict:
        """LG3 — 실린 줄의 종류별 · 층별 · 범위별 · 강제(명시 · 안전) 바이트와 추정 토큰(`est_tokens` — 앱 한도와 같은 식)."""
        import json as _json

        meta = {x["n"]: x for x in tm.new_lines if not x.get("summary")}
        acc: dict[str, dict[str, int]] = {"kind": {}, "layer": {}, "scope": {}, "forced": {}}

        def add(kind: str, key: str, b: int) -> None:
            acc[kind][key] = acc[kind].get(key, 0) + b

        for g in tm.groups:
            for r in g["rows"]:
                m = meta.get(r.get("n"))
                if m is None:
                    continue
                b = len(_json.dumps(r, ensure_ascii=False).encode("utf-8")) + 1
                add("kind", "+".join(m.get("kind") or []) or "?", b)
                add("layer", ((m.get("layer") or {}).get("role") if isinstance(m.get("layer"), dict) else None) or "-", b)
                why = m.get("fetch") or ""
                scope = self.fetch_plan.scope if self.fetch_plan else "branch"
                add("scope", "names_only" if m.get("names_only") else scope, b)
                if why in ("explicit", "safety"):
                    add("forced", why, b)
        return {k: {kk: {"bytes": vv, "est_tokens": vv // 3 + 1} for kk, vv in v.items()}  # `fold.est_tokens` 와 같은 식
                for k, v in acc.items()}

    def figure(self, body: str) -> tuple[dict | None, list[str]]:
        """CH3 — 답 안 표지 줄의 코드가 이번 턴 그림 후보면 그 하나(한 답 한 그림) · 나머지는 지움(장부 `fig.dropped`)."""
        if not self.prompt.kos:
            return None, []
        rx = fig_re(self.prompt.kos["fig_marker"])
        ok = {f["code"]: f for f in self.figs}
        fig, dropped = None, []
        for m in rx.finditer(body or ""):
            code = m.group(1)
            if fig is None and code in ok:
                fig = dict(ok[code])
            else:
                dropped.append(code)
        return fig, dropped

    async def _after(self, *, terminal: bool, reads, talk: bool, w=None) -> None:
        try:
            await self._detect()
        except Exception as e:  # noqa: BLE001 — 감지가 깨져도 답은 낸다(기록만)
            self.detect_error = {"kind": type(e).__name__, "detail": str(e)[:200]}
        try:
            await self._kos_block(reads)
        except Exception as e:  # noqa: BLE001 — 블록이 깨져도 답은 낸다(기록만)
            self.kos_meta = {"error": {"kind": type(e).__name__, "detail": str(e)[:200]}}
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
        if self.axis and self.axis.get("axis"):  # AX1 — 축이 있으면 축 질문 하나(0.2 빈 칸 질문 대신)
            self.ask, self.ask_why = None, "axis"
        self._close(w, kinds_read, talk)
        if self.axis and self.axis.get("axis"):  # 끝맺음 = 축 질문(close.by ask · 블록은 message 의 ask_view)
            self.close = {"by": "ask", "alt": [], "offered": [], "said": None}

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
        if self.axis and self.axis.get("axis"):  # AX5 — 봇 답 끝 질문 하나 · 선택지는 이 값만(0.2 `ask` 꼴)
            self.ask_view = {"slot": f"axis:{self.axis['axis']}", "about": "",
                             "options": [{"name": o["name"]} for o in self.axis["options"]]}
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
            if self.fetch_plan is not None:  # SPEC-011 AR2 — 이번 턴에 가져온 것(안 실린 종류를 「정보 없음」 으로 말하지 않게)
                extra["fetch"] = self.fetch_plan.brief()
            j = mat.judgement_v3(w, tm, self.n, kind_labels=self.prompt.kind_labels, extra=extra)
        self.close_view = close_mod.close_view(self.close, self.ask_view, self.prompt.kind_labels)
        msg = mat.message_v3(self.prompt, j, self.close_view, self.prev_view, tm, self.question,
                             mat.consult_view(self.obj))
        if self.kos_text:  # 0.5 §7-1 — 블록은 질문 머리 앞
            qh = self.prompt.turn_headers["question"]
            i = msg.rfind(qh)
            msg = (msg[:i] + self.kos_text + "\n\n" + msg[i:]) if i >= 0 else msg + "\n\n" + self.kos_text
        return msg

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
                "usage": {k: (raw.usage or {}).get(k) for k in ("input_tokens", "cache_creation_input_tokens",
                                                                "cache_read_input_tokens", "output_tokens",
                                                                "thinking_tokens")} if raw else None,
                "models": list(raw.models) if raw else [],
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
               "fetch": self.fetch_view(),  # SPEC-011 LG2
               **({"site_index": self.site_index_rec} if self.site_index_rec is not None else {}),
               "hub_queries": len(self.queries) - sum(1 for q in self.queries if q.get("skipped")), **self.ms}
        if self.dec is not None:  # 0.5 TP10 · CD3 — 주제 목록 · 지금 주제 · 고르기 장부 · 지금 주제 조건 칸
            out.update(topics=self.dec.topics, active_topic=self.dec.active, topic=self.dec.rec,
                       conditions=self.items_rec.get("conditions") if self.items_rec else None)
        if self.detect_error:
            out["detect_error"] = self.detect_error
        if self.axis is not None:
            out["ask_axis"] = self.axis
        if self.error:
            out["error"] = self.error
        if err is not None:
            out["not_counted"] = True
        return [], out
