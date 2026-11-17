"""보고서(SPEC-002 §10) — 한 batch = 보고서 하나 · 합격선 없음 · 「해당 없음」 칸을 0 으로 적지 않는다.

    uv run python -m clinic_chat.report --batch <batch_id>      → runs/reports/<batch_id>.md

표: 열별 표 두 벌(전체 / 원장 = silver — §8-5) · 범위별 · 상태별 · B 전용 · B 「추론 끔 / 켬」 비교. 값은 전부 runs · quality
줄에서 읽는다(새로 계산해 덧붙이는 점수가 없다). 범위 · 기대 종료 상태는 세트 항목 · 답 키에서.
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from clinic_chat import prompts
from clinic_chat.config import load_settings
from clinic_chat.quality.checks import NA
from clinic_chat.quality.judge import ITEMS

COLS = ("a", "b", "b_on", "c")
COL3 = ("a", "b", "c")
HEAD = {"a": "A", "b": "B", "b_on": "B 켬", "c": "C"}
STATE_ROWS = ("근거 충족", "후보 소진", "원천 미확정", "입구 없음", "범위 밖")
IN_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _med(xs: list) -> str:
    xs = [x for x in xs if isinstance(x, int | float)]
    return f"{statistics.median(xs):.0f}" if xs else "—"


def _p90(xs: list) -> str:
    xs = sorted(x for x in xs if isinstance(x, int | float))
    return f"{xs[min(len(xs) - 1, int(0.9 * len(xs)))]:.0f}" if xs else "—"


def _ratio(xs: list) -> str:
    xs = [x for x in xs if isinstance(x, int | float)]
    return f"{statistics.mean(xs):.2f}" if xs else "—"


def _cell(vals: list) -> str:
    """해당 없음만 있으면 「해당 없음」 — 0 으로 바꾸지 않는다."""
    real = [v for v in vals if v != NA and v is not None]
    if not real:
        return NA if any(v == NA for v in vals) else "—"
    return str(sum(real))


def _tok(x, keys: tuple[str, ...]) -> int:
    if isinstance(x, dict):
        return sum(int(v) for k, v in x.items() if k in keys and isinstance(v, int | float)) + \
            sum(_tok(v, keys) for v in x.values() if isinstance(v, dict | list))
    if isinstance(x, list):
        return sum(_tok(v, keys) for v in x)
    return 0


class _Ctx:
    def __init__(self, rows: list[dict], quality: list[dict], items: dict, keys: dict):
        self.rows, self.items, self.keys = rows, items, keys
        self.q_by = {q["run_id"]: q for q in quality}

    def qc(self, r: dict, col: str) -> dict | None:
        return ((self.q_by.get(r["run_id"]) or {}).get("columns") or {}).get(col)

    def per(self, rows: list[dict], col: str, f: Callable[[dict], object]) -> list:
        out = []
        for r in rows:
            q = self.qc(r, col)
            if q:
                out.append(f(q))
        return out

    def col(self, r: dict, col: str) -> dict:
        return (r.get("columns") or {}).get(col) or {}

    def expected(self, r: dict) -> str | None:
        k = self.keys.get(r.get("set_id") or "") or {}
        return k.get("expected_end_state") or (self.items.get(r.get("set_id") or "") or {}).get("expected_end_state")

    def range_of(self, r: dict) -> str:
        return (self.items.get(r.get("set_id") or "") or {}).get("range") or "—"

    def lck(self, r: dict) -> bool | None:
        q = self.q_by.get(r["run_id"])
        return None if q is None else bool(q.get("lck_ids"))


def _d(q: dict, k: str):
    return (q.get("checks") or {}).get(k)


def _sum_pair(xs: list) -> str:
    ps = [x for x in xs if isinstance(x, tuple)]
    return f"{sum(a for a, _ in ps)} / {sum(b for _, b in ps)}" if ps else NA


def _layer_line(cx: _Ctx, rows: list[dict], cols: tuple[str, ...]) -> str:
    """DU1 — 층 빠짐은 주 지표 표에 섞지 않고 한 줄 따로."""
    cells = [_sum_pair(cx.per(rows, c, lambda q: (len(_d(q, "D3")["layer_missing"]), _d(q, "D3")["layer_codes"])
                              if _d(q, "D3").get("by_class") else None)) for c in cols]
    return "- ① D3 층 빠짐(따로 · 빠진 합 / 층 코드 합): " + " · ".join(f"{HEAD[c]} {x}" for c, x in zip(cols, cells,
                                                                                                strict=True))


def _col_table(cx: _Ctx, rows: list[dict], cols: tuple[str, ...]) -> list[str]:
    lines = ["| 지표 | " + " | ".join(HEAD[c] for c in cols) + " |", "|---|" + "---|" * len(cols)]

    def row(name: str, f: Callable[[str], str]) -> None:
        lines.append(f"| {name} | " + " | ".join(f(c) for c in cols) + " |")

    for it in ITEMS:
        def cell(c: str, it=it) -> str:
            v = cx.per(rows, c, lambda q: ((q.get("judge") or {}).get("items", {}).get(it) or {}).get("verdict"))
            return f"{sum(1 for x in v if x == 'pass')}/{sum(1 for x in v if x in ('pass', 'fail'))}"
        row(f"② {it} 통과/해당", cell)
    def viol(q: dict, dk: str):
        v = _d(q, dk)
        return NA if v == NA or v is None else len(v["violations"])
    for dk in ("D1", "D2", "D4"):
        row(f"① {dk} 위반", lambda c, dk=dk: _cell(cx.per(rows, c, lambda q: viol(q, dk))))
    row("① D10 읽지 않은 코드 있는 문항(기록)", lambda c: _cell(cx.per(rows, c, lambda q: NA if _d(q, "D10") in (NA, None)
                                                                  else int(bool(_d(q, "D10")["unread_codes"])))))
    row("① D5 위반", lambda c: _cell(cx.per(rows, c, lambda q: _d(q, "D5")["violations"])))
    row("① D11 시간 섞기(G13 효과→부작용 · G14 남의 시간)", lambda c: _cell(cx.per(rows, c, lambda q: NA if _d(q, "D11")
                                                                       in (NA, None) else _d(q, "D11")["violations"])))
    row("① D3 빠진 코드 비율(평균 · 0.6 키 = 값 코드)", lambda c: _ratio(cx.per(rows, c, lambda q: _d(q, "D3")["missing_ratio"])))
    row("① D3 값 빠짐 합 / 값 코드 합(0.6 주 지표)", lambda c: _sum_pair(cx.per(rows, c, lambda q: (
        len(_d(q, "D3")["missing"]), _d(q, "D3")["key_codes"]) if _d(q, "D3").get("by_class") else None)))
    row("① D3 해당 없음(값 코드 0) 문항", lambda c: str(sum(1 for x in cx.per(rows, c, lambda q: _d(q, "D3").get("na"))
                                                    if x)))
    row("① D3 지어낸 코드", lambda c: _cell(cx.per(rows, c, lambda q: _d(q, "D3")["invented"]
                                                 if _d(q, "D3")["invented"] == NA else len(_d(q, "D3")["invented"]))))

    def d7(c: str, f: Callable[[dict], object], agg: Callable[[list], str]) -> str:
        vals = cx.per(rows, c, lambda q: _d(q, "D7"))
        if vals and all(v == NA for v in vals):
            return NA
        return agg([f(v) for v in vals if isinstance(v, dict)])
    row("① D7 빠진 행 비율(평균)", lambda c: d7(c, lambda v: v.get("missed_rows_ratio"), _ratio))
    row("① D7 빠진 코드 비율(평균)", lambda c: d7(c, lambda v: (v["missed_codes"] / v["key_codes"]) if v.get("key_codes")
                                            else None, _ratio))
    row("① D7 빠진 must_rows 수", lambda c: d7(c, lambda v: v.get("missed_must_rows"), lambda xs: str(sum(x or 0 for x in xs))))
    row("① D8 unasked 있는 문항", lambda c: str(sum(1 for x in cx.per(rows, c, lambda q: any(_d(q, "D8")["unasked"].values()))
                                              if x)))
    row("① D9 케이스 이름 나온 문항", lambda c: str(sum(1 for x in cx.per(rows, c, lambda q: _d(q, "D9")["case_names_used"]) if x)))
    row("§8-6 M1 groups 후보 빠진 수", lambda c: _cell(cx.per(rows, c, lambda q: (_d(q, "M1") or {}).get("missing"))))
    row("「원장 오류 따라감」", lambda c: str(sum(1 for x in cx.per(rows, c, lambda q: q.get("followed_ledger_error")) if x)))

    def st(c: str) -> str:
        hit = n = 0
        for r in rows:
            q = cx.qc(r, c)
            if q and cx.expected(r):
                n += 1
                hit += q.get("graded_end_state") == cx.expected(r)
        return f"{hit}/{n}"
    row("종료 상태 일치(② graded_end_state = 기대)", st)
    row("total_ms 중앙 · p90 / ttft_ms 중앙", lambda c: f"{_med([cx.col(r, c).get('timing', {}).get('total_ms') for r in rows])}"
        f" · {_p90([cx.col(r, c).get('timing', {}).get('total_ms') for r in rows])}"
        f" / {_med([cx.col(r, c).get('timing', {}).get('ttft_ms') for r in rows])}")
    row("LLM 왕복 · 도구 · 허브 · Jev 호출(중앙)", lambda c: " · ".join(
        _med([(cx.col(r, c).get("calls") or {}).get(k) for r in rows if cx.col(r, c)])
        for k in ("llm_round_trips", "tool_calls", "hub_calls", "jev_calls")))
    row("답 길이 — 출력 토큰 · LLM 입력 KB(중앙 · B 는 꼬리 포함 — N-5)", lambda c: (
        f"{_med([_tok(cx.col(r, c).get('tokens'), ('output_tokens',)) for r in rows if cx.col(r, c)])} · "
        f"{_med([((cx.col(r, c).get('trace') or {}).get('llm_input_bytes') or 0) / 1024 for r in rows if cx.col(r, c)])}"))
    row("first_answer_text_ms 중앙", lambda c: _med([cx.col(r, c).get("timing", {}).get("first_answer_text_ms")
                                                for r in rows]))
    row("입력 · 출력 토큰(중앙)", lambda c: f"{_med([_tok(cx.col(r, c).get('tokens'), IN_KEYS) for r in rows if cx.col(r, c)])}"
        f" · {_med([_tok(cx.col(r, c).get('tokens'), ('output_tokens',)) for r in rows if cx.col(r, c)])}")
    row("오류 · 시간 초과 수", lambda c: f"{sum(1 for r in rows if cx.col(r, c).get('error'))} · "
        f"{sum(1 for r in rows if '시간 초과' in str(cx.col(r, c).get('error') or ''))}")
    return lines


def _range_table(cx: _Ctx, rows: list[dict]) -> list[str]:
    names = sorted({cx.range_of(r) for r in rows})
    lines = ["| 범위 | 문항 | " + " | ".join(f"{HEAD[c]} 정확성 · 안전" for c in COL3) + " |", "|---|---:|" + "---|" * len(COL3)]
    for nm in names:
        rs = [r for r in rows if cx.range_of(r) == nm]

        def p(c: str, it: str, rs=rs) -> int:
            return sum(1 for x in cx.per(rs, c, lambda q: ((q.get("judge") or {}).get("items", {}).get(it) or {})
                                         .get("verdict")) if x == "pass")
        lines.append(f"| {nm} | {len(rs)} | " + " | ".join(f"{p(c, 'accuracy')} · {p(c, 'safety')}" for c in COL3) + " |")
    return lines


def _state_table(cx: _Ctx, rows: list[dict]) -> list[str]:
    lines = ["| 기대 종료 상태 | 문항 | " + " | ".join(HEAD[c] for c in COL3) + " |", "|---|---:|" + "---|" * len(COL3)]
    for stn in STATE_ROWS:
        rs = [r for r in rows if cx.expected(r) == stn]
        cells = [str(sum(1 for r in rs if (cx.qc(r, c) or {}).get("graded_end_state") == stn)) for c in COL3]
        lines.append(f"| {stn} | {len(rs)} | " + " | ".join(cells) + " |")
    return lines


def _ids(xs) -> set[str]:
    return {x if isinstance(x, str) else f"{x.get('table')}:{x.get('code')}" for x in xs or []}


def _b_only(cx: _Ctx, rows: list[dict]) -> list[str]:
    """B 전용 — 맨 앞 판단(대상 · 종류 일치 · Noul 분포 · met_by) · 입구 고르기 · 깊이 · gate(§10 「B 전용」)."""
    tr = [(r, cx.col(r, "b").get("trace") or {}) for r in rows if cx.col(r, "b")]
    d6 = cx.per(rows, "b", lambda q: (_d(q, "D6") or {}).get("match") if isinstance(_d(q, "D6"), dict) else None)
    said = sum(1 for r in rows if cx.qc(r, "b") and cx.col(r, "b").get("end_state")
               and cx.qc(r, "b").get("graded_end_state") != cx.col(r, "b").get("end_state"))
    tg = {"n": 0, "cover": 0, "missed": 0, "extra": 0, "dropped_expected": 0, "named": 0, "named_unexpected": 0}
    kd = {"n": 0, "cover": 0, "missed": 0, "extra": 0, "offered_expected": 0}
    rng_hit = rng_n = 0
    scope_gap = 0
    tnoul, knoul, ncand, met = Counter(), Counter(), [], Counter()
    judged = lines_ok = unused = 0
    pick_n, pick_res, pick_hit, pick_req = 0, Counter(), 0, []
    dec, closed = Counter(), Counter()
    noul = []
    complete = total_reads = 0
    forced = 0
    kfail = Counter()
    pages_gt1 = 0

    def band(v) -> str:
        return "—" if not isinstance(v, int | float) else ("yes" if v >= 0.7 else ("no" if v <= 0.3 else "사이"))
    for r, t in tr:
        f = t.get("front") or {}
        item = cx.items.get(r.get("set_id") or "") or {}
        targets = f.get("targets") or []
        got_t = {x["id"] for x in targets if x.get("decision") in ("target", "uncertain_kept", "named")}
        got_t |= {x["id"] for x in (t.get("entry") or {}).get("picked") or []}
        exp_t = _ids(item.get("expected_entries") or item.get("spec_entries"))  # 세트 칸이 비면 명세 entries(W-3)
        if exp_t:
            tg["n"] += 1
            tg["cover"] += exp_t <= got_t
            tg["missed"] += len(exp_t - got_t)
            tg["extra"] += len(got_t - exp_t)
            tg["dropped_expected"] += len({x["id"] for x in f.get("dropped") or []} & exp_t)
        nm = {x["id"] for x in targets if x.get("decision") == "named"}
        tg["named"] += len(nm)
        tg["named_unexpected"] += len(nm - exp_t) if exp_t else 0
        kinds = f.get("kinds") or []
        read_k = {k["code"] for k in kinds if k.get("decision") == "read"}
        exp_k = set(item.get("expected_kinds") or [])
        if exp_k:
            kd["n"] += 1
            kd["cover"] += exp_k <= read_k
            kd["missed"] += len(exp_k - read_k)
            kd["extra"] += len(read_k - exp_k)
            kd["offered_expected"] += len({k["code"] for k in kinds if k.get("decision") == "offered"} & exp_k)
        want = cx.range_of(r)
        if want != "—":
            rng_n += 1
            rng_hit += want in {x.get("name") for x in f.get("ranges") or []}
        scope_gap += bool(f.get("scope_gap"))
        for x in targets:
            tnoul[band(x.get("noul"))] += 1
        for k in kinds:
            knoul[band(k.get("noul"))] += 1
        ncand.append(len(targets))
        for i in f.get("required") or []:
            met[i.get("met_by") or "미충족"] += 1
        judged += bool((t.get("end_check") or {}).get("needs_judgement"))
        lines_ok += t.get("answer_lines") is not None
        unused += len((t.get("entry") or {}).get("unused") or [])
        ep = t.get("entry_pick")
        if ep:
            pick_n += 1
            pick_res[ep.get("result")] += 1
            pick_req.append(ep.get("requests"))
            got = {x["id"] for x in (t.get("entry") or {}).get("picked") or []}
            pick_hit += bool(exp_t) and exp_t == got
        for d in (t.get("depth_decisions") or {}).values():
            dec[d.get("decision")] += 1
            if d.get("closed_as"):
                closed[d["closed_as"]] += 1
            if isinstance(d.get("noul"), int | float):
                noul.append(d["noul"])
        for b in t.get("breadth") or []:
            total_reads += 1
            complete += bool(b.get("complete"))
        g = t.get("gates") or {}
        forced += sum(len(x.get("forced_gids") or []) for x in g.get("gates") or [])
        for k, v in (g.get("K") or {}).items():
            if not v.get("ok"):
                kfail[k] += 1
        pages_gt1 += len(t.get("llm_pages") or []) > 1

    def q(xs: list) -> str:
        xs = sorted(x for x in xs if isinstance(x, int | float))
        if not xs:
            return "—"
        return f"최소 {xs[0]:.2f} · 중앙 {statistics.median(xs):.2f} · 최대 {xs[-1]:.2f} (n {len(xs)})"

    def cnt(c: Counter) -> str:
        return " · ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda x: str(x[0]))) or "—"
    return [
        "| 지표 | 값 |", "|---|---|",
        f"| D6 앱 판정 일치 | {sum(1 for x in d6 if x)}/{sum(1 for x in d6 if x is not None)} |",
        f"| 답이 말한 상태(②) ≠ 앱 판정 | {said} |",
        f"| 맨 앞 판단 — 대상 ⊇ 기대 입구 | {tg['cover']}/{tg['n']} · 놓친 {tg['missed']} · 더 둔 {tg['extra']} · "
        f"dropped 중 기대 {tg['dropped_expected']} · named {tg['named']}(기대 아님 {tg['named_unexpected']}) |",
        f"| 맨 앞 판단 — 읽은 종류 ⊇ 기대 종류 | {kd['cover']}/{kd['n']} · 놓친 {kd['missed']} · 더 읽은 {kd['extra']} · "
        f"offered 중 기대 {kd['offered_expected']} |",
        f"| 끌어낸 범위에 기대 범위 | {rng_hit}/{rng_n} · scope_gap {scope_gap} |",
        f"| 대상 Noul 분포 | {cnt(tnoul)} |",
        f"| 종류 Noul 분포 | {cnt(knoul)} |",
        f"| 후보 수 | {q(ncand)} |",
        f"| required met_by | {cnt(met)} |",
        f"| (가) 판정용 조회가 돈 문항 | {judged} |",
        f"| answer_lines 가 선 문항 | {lines_ok}/{len(tr)} |",
        f"| 어느 조회에도 안 쓴 입구(`entry.unused`) | {unused} |",
        f"| 입구 고르기(①-4) | 돈 문항 {pick_n} · {cnt(pick_res)} · 기대 입구와 같음 {pick_hit} · Jev 요청 중앙 {_med(pick_req)} |",
        f"| 깊이 결정 분포 | {cnt(dec)} · uncertain 닫음 {cnt(closed)} |",
        f"| 깊이 noul 분포 | {q(noul)} |",
        f"| 읽기 조회 complete | {complete}/{total_reads} |",
        f"| gate 가 강제로 넣은 행(G1 · G3 · G7 forced) | {forced} |",
        f"| 단계 ④ K 실패 | {cnt(kfail)} |",
        f"| 단계 ⑤ 쪽 수 > 1 인 문항 | {pages_gt1} |",
    ] + _b_only_v06(cx, rows, tr)


def _b_only_v06(cx: _Ctx, rows: list[dict], tr: list) -> list[str]:
    """0.6 줄(WORK-009 §10) — 옛 줄(칸 없음)은 「—」. 수 · 문항 id 만."""
    if not any((t.get("front") or {}).get("thresholds", {}).get("kind_read") is not None for _, t in tr):
        return ["| (0.6) 종류 까닭 · 무리 · 값 고르기 · 답 확인 | — |"]
    by_cnt: Counter = Counter()
    hit = want = 0
    out_kinds, out_rows, out_ids = 0, 0, []
    kd11_n = kd11_exp = 0
    br6 = {"named": [], "uncertain_kept": []}
    br6_asked: list = []
    w4 = {"w4": [], "b3": []}
    g_one = g_many = 0
    g_calls: list = []
    vp_q, vp_req, vp_yes = [], 0, 0
    br5_q, br5_drop = [], 0
    miss_v, miss_l, zero_v, ac_n = [], [], 0, 0
    offer_q = 0
    for r, t in tr:
        f = t.get("front") or {}
        e = t.get("entry") or {}
        item = cx.items.get(r.get("set_id") or "") or {}
        sid = r.get("set_id") or r.get("run_id")
        exp_k = set(item.get("expected_kinds") or [])
        kinds = f.get("kinds") or []
        nodes = [x["id"] for x in f.get("targets") or [] if x.get("decision") in ("target", "uncertain_kept", "named")]
        cov = f.get("coverage") or {}
        for k in kinds:
            if k.get("decision") != "read":
                continue
            if k["code"] in exp_k:
                by_cnt[k.get("by") or "—"] += 1
            elif k.get("by") in ("kd9", "kd10"):
                out_kinds += 1
                out_rows += sum(sum((cov.get(n, {}).get(k["code"]) or {}).get(x) or 0
                                    for x in ("self", "lower", "inherited")) for n in nodes)
                if sid not in out_ids:
                    out_ids.append(sid)
        want += len(exp_k)
        hit += len(exp_k & {k["code"] for k in kinds if k.get("decision") == "read"})
        for o in f.get("offered") or []:
            if o.get("by") == "kd11":
                kd11_n += 1
                kd11_exp += o.get("code") in exp_k
        reads = t.get("breadth") or []
        weak = {x.get("decision") for x in f.get("targets") or [] if x.get("decision") in ("named", "uncertain_kept")
                and str(x.get("id", "")).split(":")[0] in ("site", "adverse", "condition")}
        if any(b.get("mark") == "narrowed" and b.get("tool") != "pair" for b in reads):
            for wk in weak:
                br6[wk].append(sid)
            q = cx.qc(r, "b") or {}
            br6_asked.append(((q.get("judge") or {}).get("items", {}).get("asked_only") or {}).get("verdict"))
        for b in reads:
            if b.get("widened") in w4 and sid not in w4[b["widened"]]:
                w4[b["widened"]].append(sid)
        if len(nodes) >= 2:
            gs = f.get("groups") or []
            g_one += len(gs) == 1
            g_many += len(gs) > 1
            g_calls.append(len({v.get("node") for v in t.get("visited") or [] if v.get("purpose") == "group"}))
        vp = e.get("value_pick")
        if vp:
            vp_q.append(sid)
            vp_req += vp.get("requests") or 0
            vp_yes += len(vp.get("picked") or [])
            b5 = vp.get("br5")
            if b5:
                br5_q.append(sid)
                br5_drop += max(0, (b5.get("before_rows_coverage_adverse") or 0) - (b5.get("after_detail_rows") or 0))
        ac = t.get("answer_check")
        if ac:
            ac_n += 1
            mv = len((ac.get("missing") or {}).get("value") or [])
            miss_v.append(mv)
            miss_l.append(len((ac.get("missing") or {}).get("layer") or []))
            zero_v += mv == 0
        offer_q += any(x.get("by") == "offer_name" for x in e.get("picked") or [])

    def ids(xs: list) -> str:
        return " ".join(str(x) for x in xs) or "—"
    return [
        f"| (0.6) 기대 종류 덮음 · `by` 별 | {hit}/{want} · {' · '.join(f'{k} {v}' for k, v in sorted(by_cnt.items())) or '—'} |",
        f"| (0.6 · W-3) 기대 밖으로 읽은 종류(kd9 · kd10) | {out_kinds} · coverage 어림 행 {out_rows} · 문항 {ids(out_ids)} |",
        f"| (0.6) KD11 로 `offered` 로 남긴 종류 | {kd11_n} · 그 가운데 기대 종류 {kd11_exp} |",
        f"| (0.6 · W-2) BR6 으로 넓힌 조회(named · uncertain_kept) | {len(br6['named'])} · {len(br6['uncertain_kept'])} 문항 · "
        f"② asked_only pass {sum(1 for x in br6_asked if x == 'pass')}/{sum(1 for x in br6_asked if x)} |",
        f"| (0.6 · W-4 · B-3) 한 끝을 함께 읽은 짝 | W-4 {ids(w4['w4'])} · B-3 {ids(w4['b3'])} |",
        f"| (0.6 · N-7) 무리 — 대상 둘 이상 문항 | 무리 하나 {g_one} · 둘 이상 {g_many} · 무리 판정 scope 중앙 {_med(g_calls)} · "
        f"최대 {max(g_calls) if g_calls else '—'} |",
        f"| (0.6 · N-2) 값 고르기 | 돈 문항 {ids(vp_q)} · 요청 {vp_req} · yes 값 {vp_yes} · BR5 를 좁힌 문항 {ids(br5_q)} · "
        f"줄어든 행(coverage adverse 셈 − 읽은 상세 행) {br5_drop} |",
        f"| (0.6 · U-6 · W-1) 답 확인 — 값 쪽 빠진 이름 | 합 {sum(miss_v)} · 중앙 {_med(miss_v)} · 0 인 문항 {zero_v}/{ac_n} |",
        f"| (0.6) 답 확인 — 층 쪽 빠진 이름(따로) | 합 {sum(miss_l)} · 중앙 {_med(miss_l)} |",
        f"| (0.6 · EN1) 상품 이름 입구가 선 문항 | {offer_q} |",
    ]


def _on_off(cx: _Ctx, rows: list[dict]) -> list[str]:
    lines = ["| 지표 | B 추론 끔(본 꼴) | B 추론 켬 |", "|---|---|---|"]
    t = _col_table(cx, rows, ("b", "b_on"))[2:]
    keep = ("②", "① D1", "① D2", "① D4", "① D5", "① D3 빠진", "① D8", "① D9", "종료 상태", "total_ms", "입력 · 출력")
    lines += [x for x in t if any(x.startswith("| " + k) for k in keep)]
    return lines


def render(rows: list[dict], quality: list[dict], *, title: str, plumbing: bool = False, prompt_mark: str | None = None,
           items: dict | None = None, keys: dict | None = None) -> str:
    cx = _Ctx(rows, quality, items or {}, keys or {})
    head = rows[0] if rows else {}
    models = sorted({(r.get("models") or {}).get("answer") or "" for r in rows})
    judges = sorted({str((c.get("judge") or {}).get("model")) for q in quality for c in (q.get("columns") or {}).values()
                     if (c.get("judge") or {}).get("model")})
    st = head.get("settings") or {}
    lines = [f"# {title}", ""]
    if plumbing:
        lines += ["> **합성 키 — 배관 확인 · 채점 수치 아님** — 아래 값은 점수로 옮기지 않는다.", ""]
    lines += [f"- gold_hash `{(head.get('build') or {}).get('gold_hash')}` · commits `{head.get('commits')}`",
              f"- 실제 model id: answer `{', '.join(models)}`{' · **batch 도중 바뀜**' if len(models) > 1 else ''} · "
              f"judge `{', '.join(judges) or (head.get('models') or {}).get('judge')}` · jev `{(head.get('models') or {}).get('jev')}`",
              f"- 문턱 · 설정 `{st}` · answer_reasoning `{st.get('answer_reasoning')}`",
              f"- 프롬프트 sha `{head.get('prompts')}`" + (f" · **{prompt_mark}**" if prompt_mark else ""),
              f"- 답 키 confirmed 줄 {sum(1 for r in rows if (r.get('answer_key') or {}).get('confirmed'))} / {len(rows)}"
              " (미확인 키로 채점한 줄은 없다)"]
    if st.get("answer_reasoning") == "lowest_effort":
        lines.append(f"- **단계 ⑤ 추론은 끌 수 없어 가장 낮은 effort(`{st.get('lowest_effort')}`)로 돌렸다** — 「B 추론 끔 / 켬」 은 "
                     f"`{st.get('lowest_effort')}` ↔ `{st.get('effort')}` effort 비교다(SPEC-002 §6-1 · §10).")
    silver = [r for r in rows if cx.lck(r) is False]
    for name, rs in (("전체", rows), ("원장 = silver(LCK 가 걸리지 않은 문항)", silver)):
        lines += ["", f"## 열별 표 — {name} ({len(rs)} 문항)", ""] + _col_table(cx, rs, COLS) + ["", _layer_line(cx, rs, COLS)]
    lines += ["", "## 범위별 표(② 통과 수)", ""] + _range_table(cx, rows)
    lines += ["", "## 상태별 표(② 가 읽은 종료 상태 = 기대)", ""] + _state_table(cx, rows)
    lines += ["", "## B 전용", ""] + _b_only(cx, rows)
    lines += ["", "## B 「추론 끔 / 켬」 비교(같은 verified)", ""] + _on_off(cx, rows)
    lines += ["", "D7 은 두 꼴이 같다(답 재료가 같다). 세 열 표의 B 칸은 본 꼴 값이다.",
              "", "합격선은 두지 않는다 — 1회차는 재기만 한다."]
    return "\n".join(lines) + "\n"


def main() -> None:
    from clinic_chat.evalset.answer_key import load_key
    from clinic_chat.evalset.loader import load_set
    from clinic_chat.runs import RunStore

    ap = argparse.ArgumentParser(prog="clinic_chat.report")
    ap.add_argument("--batch", required=True)
    a = ap.parse_args()
    s = load_settings()
    store = RunStore(Path(s.path(s.runs_dir)))
    rows = [r for r in store.runs() if r.get("batch_id") == a.batch]
    qs = [q for q in store.quality() if q["run_id"] in {r["run_id"] for r in rows}]
    items = load_set(s)
    keys = {sid: load_key(s, sid) for sid in {r.get("set_id") for r in rows if r.get("set_id")}}
    st = prompts.status(s)
    out = Path(s.path(s.runs_dir)) / "reports" / f"{a.batch}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows, qs, title=f"batch {a.batch}", prompt_mark=st["mark"], items=items,
                          keys={k: v for k, v in keys.items() if v}), encoding="utf-8")
    print(str(out))


if __name__ == "__main__":
    main()
