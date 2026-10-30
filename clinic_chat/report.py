"""전체 실행 보고서 — RUNS_DIR 의 runs.jsonl · quality.jsonl · batches/ 를 읽어 markdown 하나.

    uv run python -m clinic_chat.report [--batch <batch_id> ...] [--out <파일>]

출력 기본 = RUNS_DIR 옆 `build-out/fullrun-report.md`(레포 밖 · 커밋 안 함). 데이터는 고치지 않는다(읽기만).
골드 약점 분류는 결정론 키워드 규칙(아래 CAUSES) — 사람이 근거 인용을 보고 확인할 것.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from clinic_chat.config import get_settings
from clinic_chat.runs import RunStore, merge_quality

CASES = ("case1", "case2", "case3")
NAMES = {"case1": "case1 원장 MCP", "case2": "case2 골드 MCP", "case3": "case3 골드 + Jev"}
ITEMS = {"accuracy": "정확성", "safety": "안전", "candidates": "후보", "uncertainty": "불확실", "on_topic": "질문에 답함"}
UNKNOWN = re.compile(r"정보 ?없음|정보가 없|자료에 없|확인되지 않|확인할 수 없|모르|알 수 없|안내하기 어렵|찾지 못", re.IGNORECASE)
# 실패 사유 분류(순서대로 먼저 맞는 것) — Opus 실패 주장(claim) · 항목 · 답 · 흔적으로
CAUSES = [
    ("「정보 없음」인데 원장엔 있음", lambda f, ans, tr: bool(UNKNOWN.search(f["claim"] + " " + ans[:400]))
     and f.get("status") == "valid"),
    ("빠진 안전 정보", lambda f, ans, tr: f["item"] == "safety"),
    ("이름 못 찾음", lambda f, ans, tr: bool(tr.get("missing")) or bool(re.search(r"찾지 못|인식하지|이름", f["claim"]))),
    ("엉뚱한 시술", lambda f, ans, tr: bool(re.search(r"엉뚱|다른 시술|관련 없|무관|잘못된 시술|해당하지 않는 시술", f["claim"]))),
    ("빠진 연결", lambda f, ans, tr: f["item"] in ("accuracy", "on_topic", "candidates")),
    ("기타", lambda f, ans, tr: True),
]


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs)) if xs else None


def fmt_ms(x):
    return "–" if x is None else (f"{x / 1000:.1f}s" if x >= 1000 else f"{x}ms")


def cause_of(f: dict, ans: str, tr: dict) -> str:
    return next(name for name, test in CAUSES if test(f, ans, tr))


def load(store: RunStore, batches: list[str] | None) -> list[dict]:
    qual = store.latest_quality()
    out = []
    for r in store._read("runs.jsonl"):
        if batches and r.get("batch_id") not in batches:
            continue
        out.append(merge_quality(r, qual.get(r["run_id"])))
    # 같은 번호가 여럿이면 마지막(재시도 · 다시 돌린 것)
    by_no: dict = {}
    for r in out:
        by_no[r.get("question_no") or r["run_id"]] = r
    return sorted(by_no.values(), key=lambda r: str(r.get("question_no") or ""))


def build(runs: list[dict], plans: list[dict]) -> str:
    L: list[str] = []
    w = L.append
    provs = Counter(r.get("provider") for r in runs)
    v = (runs[0].get("versions") if runs else {}) or {}
    w("# hub-evals 전체 실행 보고서\n")
    w(f"- 문항 {len(runs)} · provider {dict(provs)} · 답 {v.get('answer_model')} effort {v.get('answer_effort')} · "
      f"채점 {v.get('judge_provider')} {v.get('judge_model')}(열마다 절대 채점 · 가림)")
    if runs:
        w(f"- 기간 {runs[0].get('asked_at')} ~ {max(r.get('asked_at') or '' for r in runs)} · 골드 빌드 "
          f"{v.get('gold_build_hash')} · 원장 {v.get('raw_manifest_sha16')} · 앱 {str(v.get('app_commit'))[:8]}")
    for p in plans:
        w(f"- 배치 {p['batch_id']} · {p['state']} · {p['progress']}")
    w("- 참고 답(reference_answer)은 기록에만 · 채점 기준은 원장(Opus 원장 도구 3) · 아래 사유 분류는 결정론 키워드 규칙(확인 필요)\n")

    # 1. 통과율
    w("## 1. 케이스별 항목별 통과율\n")
    w("통과율 = 통과 / (통과 + 실패) · 「해당 없음」 제외 · 괄호 = 통과/판정 수\n")
    w("| 케이스 | " + " | ".join(ITEMS.values()) + " | 평균 통과(5점) | 채점된 문항 |")
    w("|---|" + "---|" * (len(ITEMS) + 2))
    for c in CASES:
        cells = []
        for it in ITEMS:
            vs = [((r["cases"].get(c) or {}).get("judge") or {}).get("items", {}).get(it) for r in runs]
            p, f = vs.count("통과"), vs.count("실패")
            cells.append(f"{round(100 * p / (p + f))}% ({p}/{p + f})" if p + f else "–")
        pcs = [((r["cases"].get(c) or {}).get("judge") or {}).get("pass_count") for r in runs]
        pcs = [x for x in pcs if isinstance(x, int)]
        w(f"| {NAMES[c]} | " + " | ".join(cells) + f" | {round(sum(pcs) / len(pcs), 2) if pcs else '–'} | {len(pcs)} |")
    w("")

    # 2. 시간
    w("## 2. 시간(중앙값)\n")
    w("| 케이스 | 첫 글자 | 전체 | CLI 기동 | 생각 | 도구 호출 수(중앙) | 오류 열 |")
    w("|---|---|---|---|---|---|---|")
    for c in CASES:
        ts = [(r["cases"].get(c) or {}).get("timing") or {} for r in runs]
        tools = [len(((r["cases"].get(c) or {}).get("trace") or {}).get("tool_calls") or []) for r in runs]
        errs = sum(1 for r in runs if (r["cases"].get(c) or {}).get("error"))
        w(f"| {NAMES[c]} | {fmt_ms(med([t.get('ttft_ms') for t in ts]))} | {fmt_ms(med([t.get('total_ms') for t in ts]))} | "
          f"{fmt_ms(med([t.get('cli_boot_ms') for t in ts]))} | {fmt_ms(med([t.get('thinking_ms') for t in ts]))} | "
          f"{med(tools)} | {errs} |")
    w("")

    # 3. 골드 약점
    w("## 3. 골드 약점 — case2 · case3 실패를 사유별로\n")
    groups: dict[str, list] = defaultdict(list)
    for r in runs:
        for c in ("case2", "case3"):
            cr = r["cases"].get(c) or {}
            j = cr.get("judge") or {}
            tr = (cr.get("trace") or {}).get("hub") or {}
            for f in j.get("failures") or []:
                groups[cause_of(f, cr.get("answer") or "", tr)].append((r, c, f))
    for name, _ in CAUSES:
        rows = groups.get(name) or []
        if not rows:
            continue
        nos = sorted({str(r.get("question_no")) for r, _, _ in rows})
        w(f"### {name} — {len(rows)}건 · 문항 {len(nos)}\n")
        w("| 번호 | 질문 | 열 | 항목 | Opus 실패 주장 | 근거 인용(원장) |")
        w("|---|---|---|---|---|---|")
        for r, c, f in rows:
            ev = f.get("evidence") or {}
            quote = f"`{ev.get('path')}` 「{(ev.get('quote') or '')[:90]}」" + ("" if f.get("status") == "valid" else " ⚠ 근거 무효")
            w(f"| {r.get('question_no')} | {(r.get('question') or '')[:40]} | {c} | {ITEMS.get(f['item'], f['item'])} | "
              f"{_cell(f.get('claim'), 140)} | {_cell(quote, 160)} |")
        w("")

    # 4. case1 통과 · 골드 실패
    w("## 4. case1(원장)은 통과인데 골드는 실패 — 골드가 원장보다 못한 곳\n")
    w("| 번호 | 질문 | 항목 | case2 | case3 |")
    w("|---|---|---|---|---|")
    n4 = 0
    for r in runs:
        j1 = ((r["cases"].get("case1") or {}).get("judge") or {}).get("items") or {}
        for it, v1 in j1.items():
            v2 = (((r["cases"].get("case2") or {}).get("judge") or {}).get("items") or {}).get(it)
            v3 = (((r["cases"].get("case3") or {}).get("judge") or {}).get("items") or {}).get(it)
            if v1 == "통과" and "실패" in (v2, v3):
                n4 += 1
                w(f"| {r.get('question_no')} | {(r.get('question') or '')[:50]} | {ITEMS.get(it, it)} | {v2 or '–'} | {v3 or '–'} |")
    w(f"\n합 {n4}건\n")

    # 5. 모름 · 정보 없음
    w("## 5. 「모름 · 정보 없음」 답\n")
    w("| 번호 | 질문 | 열 | 답 앞부분 |")
    w("|---|---|---|---|")
    n5 = Counter()
    for r in runs:
        for c in CASES:
            ans = (r["cases"].get(c) or {}).get("answer") or ""
            m = UNKNOWN.search(ans)
            if m:
                n5[c] += 1
                s = max(0, m.start() - 30)
                w(f"| {r.get('question_no')} | {(r.get('question') or '')[:40]} | {c} | {_cell('…' + ans[s:s + 110], 140)} |")
    w(f"\n열마다 {dict(n5)}\n")

    # 6. 단어 검사(없는 이름)
    w("## 6. ① 단어 검사 — 없는 이름(허브 이름 분리 뒤)\n")
    w("| 케이스 | 없는 이름 합 | 허브 이름(뺌) | 없는 이름 예 |")
    w("|---|---|---|---|")
    for c in CASES:
        wcs = [(r, (r["cases"].get(c) or {}).get("word_check") or {}) for r in runs]
        nf = sum(x.get("not_found") or 0 for _, x in wcs)
        hb = sum(x.get("hub_names") or 0 for _, x in wcs)
        ex = [f"{r.get('question_no')}:{n['name']}" for r, x in wcs for n in x.get("names") or [] if not n.get("found")][:12]
        w(f"| {NAMES[c]} | {nf} | {hb} | {_cell(' · '.join(ex), 300)} |")
    w("")

    # 7. 오류 · 재시도
    w("## 7. 오류 · 재시도\n")
    errs = [(r, c, (r["cases"].get(c) or {}).get("error")) for r in runs for c in CASES if (r["cases"].get(c) or {}).get("error")]
    w(f"- 오류 열 {len(errs)} — 같은 꼴끼리 묶음")
    kinds: dict[str, list] = defaultdict(list)
    for r, c, e in errs:
        kinds[error_kind(e)].append((r, c, e))
    for k, rows in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
        w(f"  - **{k}** — {len(rows)}건 · 문항 {', '.join(str(r.get('question_no')) + '(' + c + ')' for r, c, _ in rows)}")
        w(f"    - 예: {_cell(rows[0][2], 220)}")
        if k in FOLLOWUP:
            w(f"    - 다음(제안): {FOLLOWUP[k]}")
    qe = [(r, c, ((r["cases"].get(c) or {}).get("judge") or {}).get("error")) for r in runs for c in CASES
          if ((r["cases"].get(c) or {}).get("judge") or {}).get("error")]
    w(f"- 채점 오류 {len(qe)}")
    for r, c, e in qe:
        w(f"  - {r.get('question_no')} {c}: {_cell(e, 200)}")
    for p in plans:
        retried = [i for i in p["items"] if i.get("attempts", 0) > 1]
        failed = [i for i in p["items"] if i["status"] == "failed"]
        w(f"- 배치 {p['batch_id']}: 재시도한 문항 {[i['key'] for i in retried]} · 실패 {[i['key'] for i in failed]} · "
          f"한도 기록 {[x for x in p.get('log', []) if 'limited_until' in x or 'sleep_until' in x]}")
    w("")
    w("> 고치지 않음 — 허브 데이터 수정은 이 보고서를 보고 코디가 따로 발주한다.")
    return "\n".join(L) + "\n"


FOLLOWUP = {
    "case3 I-09 계열 id → check_conditions(허브 400)":
        "앱 case3 I-09 경로에서 계열 id 면 check_conditions 대신 그 계열 단위 시술로 펼치거나 procedure_card(sections=checks)로 — "
        "고친 뒤 이 문항들의 case3 만 다시 돌림(실행 중엔 안 고침 · 결정론이라 재시도로 안 풀림)",
}


def error_kind(e: str) -> str:
    """오류 문구 → 묶음 이름(id · 숫자를 지운 꼴)."""
    if re.search(r"/procedures/family:[^/]+/checks .*400", e or ""):
        return "case3 I-09 계열 id → check_conditions(허브 400)"
    if "HubRestError" in (e or ""):
        return "허브 REST " + re.sub(r"[A-Za-z_]+:[A-Za-z0-9.\-_]+", "<id>", e.split("→")[0].replace("HubRestError: ", "")).strip()
    if "시간 초과" in (e or ""):
        return "시간 초과"
    if "한도" in (e or "") or "limit" in (e or "").lower():
        return "구독 한도"
    return re.sub(r"\d+", "N", (e or "")[:60])


def _cell(s, n):
    s = str(s or "").replace("\n", " ").replace("|", "／")
    return s if len(s) <= n else s[:n] + "…"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", action="append")
    ap.add_argument("--out")
    a = ap.parse_args()
    s = get_settings()
    store = RunStore(s.runs_path)
    plans = []
    bdir = s.runs_path / "batches"
    for p in sorted(bdir.glob("*.json")) if bdir.is_dir() else []:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not a.batch or d["batch_id"] in a.batch:
            plans.append(d)
    runs = load(store, a.batch)
    out = Path(a.out) if a.out else s.runs_path.parent / "build-out" / "fullrun-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(runs, plans), encoding="utf-8")
    print(f"[report] {out} · 문항 {len(runs)}")


if __name__ == "__main__":
    main()
