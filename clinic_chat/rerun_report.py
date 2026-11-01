"""재실행 전후 비교 보고서 — 전 = 10-30 전체 실행(골드 빌드 접두 --before-gold) · 후 = 일괄 라벨(--label).

    uv run python -m clinic_chat.rerun_report --questions-file <rerun-questions.txt> --label rerun-v3 \
        [--before-gold e9d30ace] [--out <md>]

출력 = md + 같은 이름 .json(레포 밖 · 기본 RUNS_DIR 옆 build-out/rerun-report.md). 숫자와 예만 — 결론 · 고칠 안 · 사유 재분류 없음.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from clinic_chat.config import get_settings
from clinic_chat.report import ITEMS, NAMES, UNKNOWN, _cell, fmt_ms
from clinic_chat.runs import RunStore, merge_quality
from clinic_chat.verify_runs import case_stats, cover34, walk_summary

CASES = ("case1", "case2", "case3")


def _med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs)) if xs else None


def pick(store: RunStore, label: str, before_gold: str, before_label: str | None = None,
         batch: str | None = None) -> tuple[dict, dict, dict]:
    """번호 → 기록(같은 번호 여럿이면 마지막). 후 = batch_label == label(batch 를 주면 그 배치만) ·
    전 = before_label 이 있으면 그 라벨 배치 · 없으면 골드 빌드 접두가 before_gold 인 라벨 없는 기록(10-30 전체 실행).
    셋째 = 239 환산용 「전 상태 전부」(10-30 전체 위에 before_label 배치를 덮음)."""
    qual = store.latest_quality()
    full, labeled, after = {}, {}, {}
    for r in store._read("runs.jsonl"):
        no = r.get("question_no")
        if not no:
            continue
        r = merge_quality(r, qual.get(r["run_id"]))
        if r.get("batch_label") == label and (batch is None or r.get("batch_id") == batch):
            after[no] = r
        elif before_label and r.get("batch_label") == before_label:
            labeled[no] = r
        elif str((r.get("versions") or {}).get("gold_build_hash") or "").startswith(before_gold) and not r.get("batch_label"):
            full[no] = r
    before = labeled if before_label else full
    return before, after, {**full, **labeled}


def items(r: dict | None, c: str) -> dict:
    return (((r or {}).get("cases") or {}).get(c) or {}).get("judge", {}).get("items") or {}


def judge(r: dict | None, c: str) -> dict:
    return (((r or {}).get("cases") or {}).get(c) or {}).get("judge") or {}


def case(r: dict | None, c: str) -> dict:
    return (((r or {}).get("cases") or {}).get(c)) or {}


def rates(runs: list[dict]) -> dict:
    """케이스 → 항목 → (통과, 판정 수)."""
    out = {}
    for c in CASES:
        out[c] = {}
        for it in ITEMS:
            vs = [items(r, c).get(it) for r in runs]
            out[c][it] = (vs.count("통과"), vs.count("통과") + vs.count("실패"))
    return out


def hub_bytes(r: dict, c: str) -> int | None:
    cr = case(r, c)
    tr = cr.get("trace") or {}
    if c == "case3":
        w = tr.get("walk")
        if w:  # 걸음 판 = 펼친 바이트 + peek 바이트(§4-4 공정성과 같은 셈)
            return (w.get("bytes_total") or 0) + sum((s.get("peek_bytes") or 0) for s in w.get("steps") or [])
        return (tr.get("hub") or {}).get("result_bytes")
    calls = tr.get("tool_calls") or []
    bs = [x.get("delivered_bytes", x.get("output_bytes")) for x in calls]
    bs = [b for b in bs if isinstance(b, int)]
    return sum(bs) if bs else None


def build(qs: list[str], before: dict, after: dict, all_before: dict, label: str, before_gold: str,
          before_desc: str = "10-30 전체 실행") -> tuple[str, dict]:
    L: list[str] = []
    w = L.append
    b_runs = [before[q] for q in qs if q in before]
    a_runs = [after[q] for q in qs if q in after]
    missing_b = [q for q in qs if q not in before]
    missing_a = [q for q in qs if q not in after]
    av = (a_runs[0].get("versions") if a_runs else {}) or {}
    bv = (b_runs[0].get("versions") if b_runs else {}) or {}
    data: dict = {"label": label, "questions": len(qs), "before_gold": bv.get("gold_build_hash"),
                  "after_gold": av.get("gold_build_hash"), "missing_before": missing_b, "missing_after": missing_a}
    w("# 재실행 전후 비교 보고서\n")
    w(f"- 대상 {len(qs)}문항 · 전 = {before_desc}(골드 {bv.get('gold_build_hash')}) {len(b_runs)}문항 · "
      f"후 = 일괄 `{label}`(골드 {av.get('gold_build_hash')}) {len(a_runs)}문항")
    w(f"- 조건: 답 {av.get('answer_provider')} {av.get('answer_model')} effort {av.get('answer_effort')} · 채점 "
      f"{av.get('judge_provider')} {av.get('judge_model')}(열마다 절대 · 가림) — 전 판과 같음")
    if missing_b or missing_a:
        w(f"- 빠진 문항: 전 {missing_b or '-'} · 후 {missing_a or '-'}")
    w("- 숫자와 예만(결론 · 고칠 안 · 사유 재분류 없음)\n")

    # 1. 항목 통과율 전후
    rb, ra = rates(b_runs), rates(a_runs)
    rest = [r for q, r in all_before.items() if q not in set(qs)]  # 재실행 안 한 문항(전 그대로)
    r239b, r239a = rates(rest + b_runs), rates(rest + a_runs)
    w("## 1. 케이스별 항목 통과율 전 → 후\n")
    w(f"분모: 「{len(qs)}문항」 = 재실행 문항만 · 「239 환산」 = 재실행 안 한 {len(rest)}문항은 전 결과 그대로 + 재실행 문항은 후 결과. "
      "통과율 = 통과 / (통과 + 실패) · 「해당 없음」 제외\n")
    w("| 케이스 | 항목 | 182 전 | 182 후 | Δ(%p) | 239 환산 전 | 239 환산 후 | Δ(%p) |")
    w("|---|---|---|---|---|---|---|---|")
    data["rates"] = {}
    for c in CASES:
        for it, nm in ITEMS.items():
            row = {}
            for tag, src in (("q_before", rb), ("q_after", ra), ("all_before", r239b), ("all_after", r239a)):
                p, n = src[c][it]
                row[tag] = {"pass": p, "n": n, "pct": round(100 * p / n, 1) if n else None}
            d1 = _d(row["q_before"]["pct"], row["q_after"]["pct"])
            d2 = _d(row["all_before"]["pct"], row["all_after"]["pct"])
            w(f"| {NAMES[c]} | {nm} | {_p(row['q_before'])} | {_p(row['q_after'])} | {d1} | {_p(row['all_before'])} | "
              f"{_p(row['all_after'])} | {d2} |")
            data["rates"].setdefault(c, {})[it] = row
    w("")

    # 2. 통과 수 · 정보 없음 · 오류 · 시간 · 허브 바이트
    w("## 2. 문항당 통과 수 · 「정보 없음」류 답 · 오류 · 시간 · 허브 결과 바이트 — 전 → 후\n")
    w("| 케이스 | 평균 통과(5) | 「정보 없음」류 답 | 오류 열 | 첫 글자 중앙 | 전체 중앙 | 허브 결과 바이트 중앙 |")
    w("|---|---|---|---|---|---|---|")
    data["summary"] = {}
    for c in CASES:
        s = {}
        for tag, runs in (("before", b_runs), ("after", a_runs)):
            pcs = [judge(r, c).get("pass_count") for r in runs if isinstance(judge(r, c).get("pass_count"), int)]
            s[tag] = {
                "avg_pass": round(sum(pcs) / len(pcs), 2) if pcs else None,
                "unknown_answers": sum(1 for r in runs if UNKNOWN.search(case(r, c).get("answer") or "")),
                "errors": sum(1 for r in runs if case(r, c).get("error")),
                "ttft_ms": _med([(case(r, c).get("timing") or {}).get("ttft_ms") for r in runs]),
                "total_ms": _med([(case(r, c).get("timing") or {}).get("total_ms") for r in runs]),
                "hub_bytes": _med([hub_bytes(r, c) for r in runs]),
            }
        data["summary"][c] = s
        b, a = s["before"], s["after"]
        w(f"| {NAMES[c]} | {b['avg_pass']} → {a['avg_pass']} | {b['unknown_answers']} → {a['unknown_answers']} | "
          f"{b['errors']} → {a['errors']} | {fmt_ms(b['ttft_ms'])} → {fmt_ms(a['ttft_ms'])} | "
          f"{fmt_ms(b['total_ms'])} → {fmt_ms(a['total_ms'])} | {b['hub_bytes']} → {a['hub_bytes']} |")
    w("\n허브 결과 바이트: case1 · case2 = 그 열 도구 결과 바이트 합(모델이 받은 delivered) · case3 = 허브 경로 결과 바이트"
      "(trace.hub.result_bytes) · 걸음 판은 bytes_total + peek\n")

    # 2-1. 도구 전달 · intent · 격리 · 걸음(SPEC-006 v2 §3-6 · §4-4 · 기록 칸만)
    w("## 2-1. intent 전달 · 잘린 호출(마커) · 격리 밖 도구 · case3 걸음 — 전 → 후\n")
    w("| 케이스 | intent 전달 비율 | 잘린 호출(마커) | 격리 밖 도구 | 걸음(판 · 되돌아감 · 걸음 중앙 · bytes_total 중앙 · Jev 호출) |")
    w("|---|---|---|---|---|")
    data["delivery"] = {}
    for c in CASES:
        sb, sa = case_stats(b_runs, c), case_stats(a_runs, c)
        data["delivery"][c] = {"before": sb, "after": sa}
        ir = lambda st: f"{st['intent_ratio']:.1%} ({st['intent_passed']}/{st['intent_calls']})" \
            if st["intent_calls"] else "해당 없음"
        wk = lambda st: (f"{st['walk']['runs']} · {st['walk']['fallback']} · {st['walk']['steps_median']} · "
                         f"{st['walk']['bytes_total_median']} · {st['walk']['jev_calls']}") if st["walk"] else "–"
        w(f"| {NAMES[c]} | {ir(sb)} → {ir(sa)} | {sb['truncated_marker']} → {sa['truncated_marker']} | "
          f"{sb['stray']} → {sa['stray']} | {wk(sb)} → {wk(sa)} |")
    w("\n잘림 · C 비율의 허브 재호출 대조는 `clinic_chat.verify_runs`(§4-4)\n")

    # 3. 나빠진 문항
    w("## 3. 나빠진 문항 — 전 통과 → 후 실패\n")
    w("| 번호 | 질문 | 열 | 항목 | 후 Opus 실패 주장 | 근거 경로 · 인용 |")
    w("|---|---|---|---|---|---|")
    worse = []
    for q in qs:
        for c in CASES:
            bi, ai = items(before.get(q), c), items(after.get(q), c)
            for it in ITEMS:
                if bi.get(it) == "통과" and ai.get(it) == "실패":
                    f = next((x for x in judge(after.get(q), c).get("failures") or [] if x["item"] == it), {})
                    ev = f.get("evidence") or {}
                    worse.append({"no": q, "case": c, "item": it, "claim": f.get("claim"), "evidence": ev,
                                  "evidence_status": f.get("status")})
                    w(f"| {q} | {_cell((after.get(q) or {}).get('question'), 40)} | {c} | {ITEMS[it]} | "
                      f"{_cell(f.get('claim'), 160)} | {_cell(_ev(f), 160)} |")
    w(f"\n합 {len(worse)}건\n")
    data["worse"] = worse

    # 4. 문항 × 케이스 전후 표
    w("## 4. 문항 × 케이스 전후(항목 ●통과 ✕실패 –해당 없음 · 통과 수 전 → 후 · Δ)\n")
    w("항목 순서: " + " · ".join(ITEMS.values()) + "\n")
    w("| 번호 | " + " | ".join(f"{c} 전" for c in CASES) + " | " + " | ".join(f"{c} 후" for c in CASES) + " | " +
      " | ".join(f"{c} Δ" for c in CASES) + " |")
    w("|---|" + "---|" * 9)
    data["per_question"] = {}
    mark = {"통과": "●", "실패": "✕", "해당 없음": "–"}
    for q in qs:
        row = {}
        cells_b, cells_a, deltas = [], [], []
        for c in CASES:
            bi, ai = items(before.get(q), c), items(after.get(q), c)
            pb, pa = judge(before.get(q), c).get("pass_count"), judge(after.get(q), c).get("pass_count")
            eb, ea = case(before.get(q), c).get("error"), case(after.get(q), c).get("error")
            cells_b.append("오류" if eb else "".join(mark.get(bi.get(it), "·") for it in ITEMS) + f" {pb}")
            cells_a.append("오류" if ea else "".join(mark.get(ai.get(it), "·") for it in ITEMS) + f" {pa}")
            d = (pa - pb) if isinstance(pa, int) and isinstance(pb, int) else None
            deltas.append("–" if d is None else f"{d:+d}")
            row[c] = {"before": {"items": bi, "pass": pb, "error": eb}, "after": {"items": ai, "pass": pa, "error": ea},
                      "delta": d}
        data["per_question"][q] = row
        w(f"| {q} | " + " | ".join(cells_b) + " | " + " | ".join(cells_a) + " | " + " | ".join(deltas) + " |")
    w("")

    # 5. 남은 실패(후) — 주장 · 근거 그대로
    w("## 5. 후에도 실패 — Opus 주장 · 근거 경로 그대로(재분류 안 함)\n")
    w("| 번호 | 열 | 항목 | Opus 실패 주장 | 근거 경로 · 인용 |")
    w("|---|---|---|---|---|")
    left = []
    for q in qs:
        for c in CASES:
            for f in judge(after.get(q), c).get("failures") or []:
                left.append({"no": q, "case": c, "item": f["item"], "claim": f.get("claim"), "evidence": f.get("evidence"),
                             "evidence_status": f.get("status")})
                w(f"| {q} | {c} | {ITEMS.get(f['item'], f['item'])} | {_cell(f.get('claim'), 160)} | {_cell(_ev(f), 160)} |")
    w(f"\n합 {len(left)}건 · 열마다 {dict(Counter(x['case'] for x in left))}\n")
    data["remaining_failures"] = left

    # 6. 오류
    w("## 6. 오류 열 — 전 → 후\n")
    for tag, src in (("전", before), ("후", after)):
        errs = [(q, c, case(src.get(q), c).get("error")) for q in qs for c in CASES if case(src.get(q), c).get("error")]
        w(f"- {tag} {len(errs)}: " + (" · ".join(f"{q}({c})" for q, c, _ in errs) or "없음"))
        for q, c, e in errs[:5]:
            w(f"  - {q} {c}: {_cell(e, 200)}")
    return "\n".join(L) + "\n", data


# ── 2회 평균 · case1 대비 차(SPEC-006 §4-5 v2.2) ─────────────────────────────────────────
W4 = {"accuracy": 58.0, "safety": 90.0, "ttft_ms": 7000}  # case3 통과 기준(사용자 11-01 · W-4)


def pick_batch(store: RunStore, batch: str) -> dict:
    """배치 id → 번호별 기록(채점 붙임 · 같은 번호 여럿이면 마지막)."""
    qual = store.latest_quality()
    out = {}
    for r in store._read("runs.jsonl"):
        if r.get("batch_id") == batch and r.get("question_no"):
            out[r["question_no"]] = merge_quality(r, qual.get(r["run_id"]))
    return out


def ran(r: dict | None, c: str) -> bool:
    cr = case(r, c)
    return bool(cr) and not cr.get("skipped")


def one_batch(qs: list[str], runs: dict) -> dict:
    """한 회 — 케이스별 항목 통과율 · case1 대비 차 Δ(같은 배치 · case1 을 돌렸을 때만) · 평균 통과 · 「정보 없음」 · 첫 글자."""
    rs = [runs[q] for q in qs if q in runs]
    out: dict = {"n": len(rs), "cases": {}}
    for c in CASES:
        cr = [r for r in rs if ran(r, c)]
        if not cr:
            continue
        rt = rates(cr)[c]
        pcs = [judge(r, c).get("pass_count") for r in cr if isinstance(judge(r, c).get("pass_count"), int)]
        out["cases"][c] = {
            "runs": len(cr), "rates": {it: {"pass": p, "n": n, "pct": round(100 * p / n, 1) if n else None}
                                       for it, (p, n) in rt.items()},
            "avg_pass": round(sum(pcs) / len(pcs), 2) if pcs else None,
            "unknown_answers": sum(1 for r in cr if UNKNOWN.search(case(r, c).get("answer") or "")),
            "errors": sum(1 for r in cr if case(r, c).get("error")),
            "ttft_ms": _med([(case(r, c).get("timing") or {}).get("ttft_ms") for r in cr]),
            "total_ms": _med([(case(r, c).get("timing") or {}).get("total_ms") for r in cr]),
            "hub_bytes": _med([hub_bytes(r, c) for r in cr])}
    base = out["cases"].get("case1")
    for c, x in out["cases"].items():
        x["delta_case1"] = {it: (round(v["pct"] - base["rates"][it]["pct"], 1)
                                 if base and v["pct"] is not None and base["rates"][it]["pct"] is not None else None)
                            for it, v in x["rates"].items()} if c != "case1" else None
    w = walk_summary(rs)
    if w:
        w["cover34"] = {k: {x: v[x] for x in ("rows", "denominator", "shown", "picked", "got", "other_start")}
                        for k, v in cover34(rs).items() if isinstance(v, dict)}
    out["walk"] = w
    out["stray"] = {c: case_stats(rs, c)["stray_by_kind"] for c in ("case1", "case2") if any(ran(r, c) for r in rs)}
    return out


def flips(qs: list[str], a: dict, b: dict, c: str) -> dict:
    """두 회 사이 같은 문항 · 같은 항목 판정(통과 · 실패)이 바뀐 비율."""
    n = k = 0
    for q in qs:
        ia, ib = items(a.get(q), c), items(b.get(q), c)
        for it in ITEMS:
            if ia.get(it) in ("통과", "실패") and ib.get(it) in ("통과", "실패"):
                n += 1
                k += ia[it] != ib[it]
    return {"flipped": k, "judged": n, "ratio": round(k / n, 4) if n else None}


def build_two(qs: list[str], before: dict, batches: dict[str, dict], before_desc: str) -> tuple[str, dict]:
    """--batches a,b — 2회 평균 · 두 회 차 · 뒤집힘 · 전(1회) 대비 · case3 통과 기준(W-4) · 걸음 칸(§7-8)."""
    L: list[str] = []
    w = L.append
    names = list(batches)
    per = {b: one_batch(qs, batches[b]) for b in names}
    pre = one_batch(qs, before)
    data: dict = {"questions": len(qs), "before": before_desc, "batches": names, "per_batch": per, "before_stats": pre}
    cases = [c for c in CASES if all(c in per[b]["cases"] for b in names)]
    w("# 재실행 보고 — 2회 평균(SPEC-006 §4-5 · §7-8)\n")
    av = next((r.get("versions") for r in batches[names[0]].values()), {}) or {}
    st = next((r.get("settings") for r in batches[names[0]].values()), {}) or {}
    w(f"- 대상 {len(qs)}문항 · 후 = 배치 {' · '.join(f'`{b}`({per[b]["n"]}판)' for b in names)} · 전 = {before_desc}(1회 · 「전 1회」)")
    w(f"- 후 조건: 앱 {av.get('app_commit')} · 골드 {av.get('gold_build_hash')} · 답 {av.get('answer_provider')} "
      f"{av.get('answer_model')} · 프롬프트 case3 {(av.get('prompts') or {}).get('case3')} · 순서 표 {st.get('order_table_sha')} · "
      f"돌린 케이스 {next((r.get('cases_run') for r in batches[names[0]].values()), None) or '전부'}")
    w("- 숫자만(결론 없음) · 통과율 = 통과 / (통과 + 실패)\n")
    w("## 1. 항목 통과율 — 전 · 후 각 회 · 2회 평균 · 두 회 차 · case1 대비 차\n")
    w("| 케이스 | 항목 | 전 | " + " | ".join(f"후 {i + 1}" for i in range(len(names))) +
      " | 2회 평균 | 두 회 차(%p) | 평균 − 전(%p) | case1 대비 차(2회 평균) |")
    w("|---|---|---|" + "---|" * len(names) + "---|---|---|---|")
    data["rates"] = {}
    for c in cases:
        for it, nm in ITEMS.items():
            vals = [per[b]["cases"][c]["rates"][it]["pct"] for b in names]
            avg = round(sum(vals) / len(vals), 1) if all(v is not None for v in vals) else None
            diff = round(abs(vals[0] - vals[1]), 1) if len(vals) == 2 and None not in vals else None
            bp = ((pre["cases"].get(c) or {}).get("rates") or {}).get(it, {}).get("pct")
            dd = [per[b]["cases"][c]["delta_case1"][it] for b in names] if c != "case1" else []
            d1 = round(sum(dd) / len(dd), 1) if dd and None not in dd else None
            data["rates"].setdefault(c, {})[it] = {"before": bp, "after": vals, "avg": avg, "diff": diff,
                                                   "vs_before": _d(bp, avg), "delta_case1_avg": d1}
            cells = [_p(per[b]["cases"][c]["rates"][it]) for b in names]
            w(f"| {NAMES[c]} | {nm} | {_p(pre['cases'][c]['rates'][it]) if c in pre['cases'] else '–'} | " + " | ".join(cells) +
              f" | {avg if avg is not None else '–'}% | {diff if diff is not None else '–'} | {_d(bp, avg)} | "
              f"{'–' if d1 is None else f'{d1:+.1f}'} |")
    w("\ncase1 대비 차 = 같은 배치 안 통과율(caseN) − 통과율(case1) — case1 을 안 돌린 배치(`--cases case3`)는 –\n")
    w("## 2. 뒤집힘 — 두 회 사이 같은 문항 · 같은 항목 판정이 바뀐 비율\n")
    data["flips"] = {c: flips(qs, batches[names[0]], batches[names[1]], c) for c in cases} if len(names) == 2 else {}
    for c, f in data["flips"].items():
        w(f"- {NAMES[c]}: {f['flipped']}/{f['judged']} = {pct(f['flipped'], f['judged'])}")
    w("\n## 3. 평균 통과 · 「정보 없음」류 · 오류 · 시간 · 허브 바이트(전 → 후 각 회)\n")
    w("| 케이스 | 평균 통과(5) | 「정보 없음」류 | 오류 | 첫 글자 중앙 | 전체 중앙 | 허브 바이트 중앙 |")
    w("|---|---|---|---|---|---|---|")
    for c in cases:
        b0 = pre["cases"].get(c) or {}
        def f(k, fm=str, b0=b0, c=c):
            return " → ".join([fm(b0.get(k)) if b0 else "–"] + [fm(per[b]["cases"][c][k]) for b in names])
        w(f"| {NAMES[c]} | {f('avg_pass')} | {f('unknown_answers')} | {f('errors')} | {f('ttft_ms', fmt_ms)} | "
          f"{f('total_ms', fmt_ms)} | {f('hub_bytes')} |")
    w("\n## 4. case3 통과 기준(W-4 · 사용자 11-01) — 정확성 ≥ 58% · 안전 ≥ 90% · 첫 글자 ≤ 7초\n")
    w("| 회 | 정확성 | 안전 | 첫 글자 중앙 | 통과 |")
    w("|---|---|---|---|---|")
    data["w4"] = {}
    for b in names + ["2회 평균"]:
        if b == "2회 평균":
            x = data["rates"].get("case3") or {}
            acc, saf = (x.get("accuracy") or {}).get("avg"), (x.get("safety") or {}).get("avg")
            tt = _med([per[n]["cases"]["case3"]["ttft_ms"] for n in names]) if "case3" in cases else None
        else:
            c3 = per[b]["cases"].get("case3") or {}
            acc, saf, tt = ((c3.get("rates") or {}).get("accuracy") or {}).get("pct"), \
                ((c3.get("rates") or {}).get("safety") or {}).get("pct"), c3.get("ttft_ms")
        ok = None if None in (acc, saf, tt) else (acc >= W4["accuracy"] and saf >= W4["safety"] and tt <= W4["ttft_ms"])
        data["w4"][b] = {"accuracy": acc, "safety": saf, "ttft_ms": tt, "pass": ok}
        w(f"| {b} | {acc}% | {saf}% | {fmt_ms(tt)} | {'○' if ok else '✗' if ok is False else '–'} |")
    w("\n## 5. case3 걸음(§7-8 끝 조건 칸) — 전 → 후 각 회\n")
    rows = [("판 · 걸음 판 · 되돌아감", lambda x: f"{x['runs']} · {x['walked']} · {x['fallback']} {x['fallback_reasons']}"),
            ("걸음 중앙(판 기준 · ≥ 3)", lambda x: f"{x['steps_median']}"),
            ("예산 멈춤(≤ 30%)", lambda x: f"{x['budget_stop']}/{x['runs']} = {pct(x['budget_stop'], x['runs'])}"),
            ("멈춤 분포", lambda x: f"{x['stop']}"),
            ("이웃 fact 도달(≥ 80%)", lambda x: f"{x['neighbor_fact_reach']}/{x['walked']} = {pct(x['neighbor_fact_reach'], x['walked'])}"),
            ("이웃 peek 도달", lambda x: f"{x['neighbor_peek_reach']}/{x['walked']} = {pct(x['neighbor_peek_reach'], x['walked'])}"),
            ("2홉 fact 도달", lambda x: f"{x['hop2_fact_reach']}/{x['walked']}"),
            ("34 행 1홉 몫(뜸/분모 · 고름 · 받음)", lambda x: _c34(x, "one_hop")),
            ("34 행 0홉 몫(뜸/분모 · 고름 · 받음)", lambda x: _c34(x, "zero_hop")),
            ("bytes_mismatch(0)", lambda x: f"{x['bytes_mismatch']}"),
            ("bytes_total 중앙 · p95", lambda x: f"{x['bytes_total_median']} · {x['bytes_total_p95']}"),
            ("full.bytes 중앙 · p95 · 예산 넘음 · 못 받은 cut", lambda x: f"{x['full_bytes_median']} · {x['full_bytes_p95']} · "
                                                              f"{x['full_over_budget']} · {x['full_cut_unfetched']}"),
            ("목록 호출 · 바이트 중앙 · ms 중앙", lambda x: f"{x['list_calls']} · {x['list_bytes_median']} · {x['list_ms_median']}"),
            ("excluded · filtered_by_budget · peek 재사용 · by_unit", lambda x: f"{x['excluded']} · {x['filtered_by_budget']} · "
                                                                       f"{x['peek_reused']} · {x['by_unit_queued']}"),
            ("필수 묶음 판 · fact · 바이트 중앙 · 뺌", lambda x: f"{x['required_runs']} · {x['required_facts']} · "
                                                         f"{x['required_bytes_median']} · {x['required_filtered']}"),
            ("고정 섹션 판 · 뺌 / 「충분」 무시", lambda x: f"{x['fixed_runs']} · {x['fixed_filtered']} / {x['enough_ignored']}"),
            ("의도 묶음 판 · 섹션 / 채우기 판 · 섹션 · 바이트 중앙", lambda x: f"{x['base_bundle_runs']} · {x['bundle_sections']} / "
                                                                 f"{x['fill_runs']} · {x['fill_sections']} · {x['fill_bytes_median']}"),
            ("판마다 받은 섹션 중앙", lambda x: f"{x['sections_per_run_median']}"),
            ("Jev 호출 합 · Jev ms 중앙", lambda x: f"{x['jev_calls']} · {x['jev_ms_median']}")]
    w("| 칸 | 전 | " + " | ".join(f"후 {i + 1}" for i in range(len(names))) + " |")
    w("|---|---|" + "---|" * len(names))
    for nm, fn in rows:
        cells = [_safe(fn, pre.get("walk"))] + [_safe(fn, per[b].get("walk")) for b in names]
        w(f"| {nm} | " + " | ".join(cells) + " |")
    w("\n전 기록(v2.1)에는 step1_options · list · full 칸이 없어 – 또는 0 으로 나옴\n")
    if any(per[b]["stray"] for b in names):
        w("## 6. stray(bare_hub · foreign)\n")
        for b in names:
            w(f"- {b}: {per[b]['stray']}")
    return "\n".join(L) + "\n", data


def _c34(x: dict, k: str) -> str:
    c = (x.get("cover34") or {}).get(k)
    return f"{c['shown']}/{c['denominator']} · {c['picked']} · {c['got']}(행 {c['rows']} · S 다름 {len(c['other_start'])})" if c else "–"


def _safe(fn, x) -> str:
    if not x:
        return "–"
    try:
        return fn(x)
    except (KeyError, TypeError):
        return "–"


def pct(a, b) -> str:
    return f"{a / b:.1%}" if b else "–"


def _p(x: dict) -> str:
    return f"{round(x['pct'])}% ({x['pass']}/{x['n']})" if x["n"] else "–"


def _d(a, b) -> str:
    return "–" if a is None or b is None else f"{b - a:+.1f}"


def _ev(f: dict) -> str:
    ev = f.get("evidence") or {}
    return f"`{ev.get('path')}` 「{(ev.get('quote') or '')[:90]}」" + ("" if f.get("status") == "valid" else " ⚠ 근거 무효")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions-file", required=True)
    ap.add_argument("--label", default="rerun-v3")
    ap.add_argument("--before-gold", default="e9d30ace")
    ap.add_argument("--before-label", help="전 = 이 라벨 배치(예 rerun-v3) · 없으면 10-30 전체 실행")
    ap.add_argument("--batch", help="후 = 이 배치 id 만(같은 라벨 배치가 여럿일 때)")
    ap.add_argument("--batches", help="후 = 배치 id 둘(쉼표) — 2회 평균 · 두 회 차 · 뒤집힘 · W-4(§4-5)")
    ap.add_argument("--before-batch", help="전 = 이 배치 id(예 rerun-v4 b202611011148-c6ed3a)")
    ap.add_argument("--out")
    a = ap.parse_args()
    s = get_settings()
    if a.batches:
        store = RunStore(s.runs_path)
        qs = [x.strip() for x in Path(a.questions_file).read_text(encoding="utf-8").splitlines() if x.strip()]
        bs = {b: pick_batch(store, b) for b in [x.strip() for x in a.batches.split(",") if x.strip()]}
        before = pick_batch(store, a.before_batch) if a.before_batch else pick(store, "-", a.before_gold, a.before_label)[0]
        md, data = build_two(qs, before, bs, f"배치 `{a.before_batch}`" if a.before_batch else f"일괄 `{a.before_label}`")
        out = Path(a.out) if a.out else s.runs_path.parent / "build-out" / "rerun-report-2x.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        out.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"[rerun-report] {out} · 후 " + " · ".join(f"{b} {len(v)}" for b, v in bs.items()))
        return
    qs = [x.strip() for x in Path(a.questions_file).read_text(encoding="utf-8").splitlines() if x.strip()]
    before, after, all_before = pick(RunStore(s.runs_path), a.label, a.before_gold, a.before_label, a.batch)
    md, data = build(qs, before, after, all_before, a.label, a.before_gold,
                     before_desc=f"일괄 `{a.before_label}`" if a.before_label else "10-30 전체 실행")
    out = Path(a.out) if a.out else s.runs_path.parent / "build-out" / "rerun-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[rerun-report] {out} · 전 {len([q for q in qs if q in before])} · 후 {len([q for q in qs if q in after])}")


if __name__ == "__main__":
    main()
