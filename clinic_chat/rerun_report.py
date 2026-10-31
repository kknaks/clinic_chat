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

CASES = ("case1", "case2", "case3")


def _med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs)) if xs else None


def pick(store: RunStore, label: str, before_gold: str) -> tuple[dict, dict]:
    """번호 → 기록(같은 번호 여럿이면 마지막). 전 = 골드 빌드 접두가 before_gold · 후 = batch_label == label."""
    qual = store.latest_quality()
    before, after = {}, {}
    for r in store._read("runs.jsonl"):
        no = r.get("question_no")
        if not no:
            continue
        r = merge_quality(r, qual.get(r["run_id"]))
        if r.get("batch_label") == label:
            after[no] = r
        elif str((r.get("versions") or {}).get("gold_build_hash") or "").startswith(before_gold) and not r.get("batch_label"):
            before[no] = r
    return before, after


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
    if c == "case3":
        return ((cr.get("trace") or {}).get("hub") or {}).get("result_bytes")
    calls = (cr.get("trace") or {}).get("tool_calls") or []
    bs = [x.get("output_bytes") for x in calls if isinstance(x.get("output_bytes"), int)]
    return sum(bs) if bs else None


def build(qs: list[str], before: dict, after: dict, all_before: dict, label: str, before_gold: str) -> tuple[str, dict]:
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
    w(f"- 대상 {len(qs)}문항 · 전 = 10-30 전체 실행(골드 {bv.get('gold_build_hash')}) {len(b_runs)}문항 · "
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
    w("\n허브 결과 바이트: case1 · case2 = 그 열 도구 결과 바이트 합 · case3 = 허브 경로 결과 바이트(trace.hub.result_bytes)\n")

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
    ap.add_argument("--out")
    a = ap.parse_args()
    s = get_settings()
    qs = [x.strip() for x in Path(a.questions_file).read_text(encoding="utf-8").splitlines() if x.strip()]
    before, after = pick(RunStore(s.runs_path), a.label, a.before_gold)
    md, data = build(qs, before, after, before, a.label, a.before_gold)
    out = Path(a.out) if a.out else s.runs_path.parent / "build-out" / "rerun-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[rerun-report] {out} · 전 {len([q for q in qs if q in before])} · 후 {len([q for q in qs if q in after])}")


if __name__ == "__main__":
    main()
