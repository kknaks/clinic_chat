"""KD8 — Jev 정보 종류 판단 확인(SPEC-002 §3-1 KD8 · 허브 · LLM 없이 Jev 만).

문항마다 Jev **한 요청**: 정보 종류 14 의 `kind_{k}` Noul(§3-2a 영어 이름 · instructions 영어).
대상은 state 에 고정한다(대상 판단은 범위 밖). 두 판으로 돈다:
  ko    — state.question = 한국어 질문 그대로
  ko_en — 위 + state.question_en = 영어 한 줄 풀이(한국어 약점이 원인인지 가른다)

질문 셋은 로컬 파일(gitignore — 문장 커밋 금지)에서 읽는다. 한 줄 꼴:
  {"id", "group": single|pair|open, "shape", "target": {"table", "name"}, "expected": [kind…], "q", "en"}

  uv run python -m clinic_chat.experiments.kd8_kind_check --set data/experiments/kd8/questions.json
  (--dry-run: 요청 꼴만 출력 · Jev 호출 없음)

결과(전 Noul 값)는 --out(기본: 세트 옆 results-<시각>.json)에, 표(문장 없음 — 꼴 · 기대 종류 · 수치)는 stdout 에.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from clinic_chat.clients.jev_client import Jev, JevError, make_client, noul
from clinic_chat.config import load_settings

# SPEC-002 §3-2a — (kind, Jev 에 주는 영어 이름, 범위)
KINDS: tuple[tuple[str, str, str], ...] = (
    ("overview", "what it is (overview, category, device/material/method)", "procedure"),
    ("mechanism", "how it works (mechanism / principle)", "procedure"),
    ("indication", "what it is used for / what is done for a concern", "procedure"),
    ("outcome", "effects and how long they take / last", "procedure"),
    ("lineup", "what belongs under it (products, procedures, cases)", "procedure"),
    ("adverse", "side effects / adverse events", "adverse"),
    ("recovery", "recovery / downtime", "adverse"),
    ("contraindication", "who should not have it (patient conditions)", "avoid"),
    ("avoid", "what must not be combined with it", "avoid"),
    ("combo", "what goes well together with it", "combo"),
    ("package", "packages / programs that include it or target a concern", "combo"),
    ("same_day", "whether things can be done on the same day", "same_day"),
    ("order_interval", "order, interval, repeat interval", "order_interval"),
    ("offer", "products for sale and prices", "offer"),
)
KIND_CODES = [k for k, _, _ in KINDS]
RANGE_OF = {k: r for k, _, r in KINDS}
# §3-2a 「기본」 칸 — 대상 4표(시술 쪽 대상) 기준
DEFAULTS = {"procedure": {"overview"}, "adverse": {"adverse"}, "avoid": {"contraindication", "avoid"},
            "combo": {"combo", "package"}, "same_day": {"same_day"}, "order_interval": {"order_interval"},
            "offer": {"offer"}}

INSTR = ("Is `kinds_catalog[{k}]` needed to answer `question` about `target`? "
         "Say yes only if the question asks for this kind of information; "
         "do not count information that would merely be nice to have.")
INSTR_EN = " `question_en` is an English paraphrase of `question`."

MODES = ("ko", "ko_en")


NAME_OVERRIDE: dict[str, str] = {}  # --name kind=영어 이름(이름 문구 가설 확인용 · 기본은 §3-2a 그대로)


def build_state(item: dict, mode: str) -> dict:
    st: dict = {"question": item["q"]}
    if mode == "ko_en":
        st["question_en"] = item["en"]
    st["target"] = {"table": item["target"]["table"], "name": item["target"]["name"]}
    st["kinds_catalog"] = [{"code": k, "name": NAME_OVERRIDE.get(k, n)} for k, n, _ in KINDS]
    return st


INSTR_INLINE = ("Is the information kind \"{name}\" (`kinds_catalog[{k}]`) needed to answer `question` about `target`? "
                "Say yes only if the question asks for this kind of information; "
                "do not count information that would merely be nice to have.")
INLINE = {"on": False}  # --inline: 종류 이름을 instructions 에 직접 적는다(색인 참조만 쓰는 것과 가르기)


def build_questions(mode: str) -> dict:
    extra = INSTR_EN if mode == "ko_en" else ""
    if INLINE["on"]:
        return {f"kind_{k}": noul(INSTR_INLINE.format(k=i, name=NAME_OVERRIDE.get(k, n)) + extra)
                for i, (k, n, _) in enumerate(KINDS)}
    return {f"kind_{k}": noul(INSTR.format(k=i) + extra) for i, k in enumerate(KIND_CODES)}


def kd3_read(vals: dict[str, float], yes_at: float) -> set[str]:
    """KD1 · KD2 · KD3 본문: yes ∪ 1위 ∪ 그 종류들이 끌어낸 범위의 기본 묶음(KD5 · KD6 은 이 시험 밖)."""
    top = max(vals, key=vals.__getitem__)
    picked = {k for k, v in vals.items() if v >= yes_at} | {top}
    out = set(picked)
    for k in picked:
        out |= DEFAULTS[RANGE_OF[k]]
    return out


def score(item: dict, vals: dict[str, float], yes_at: float, no_at: float) -> dict:
    order = sorted(vals, key=lambda k: -vals[k])
    rank = {k: i + 1 for i, k in enumerate(order)}
    exp = item["expected"]
    unasked = [k for k in KIND_CODES if k not in exp]
    between = [k for k in order if no_at < vals[k] < yes_at]
    # 「사이 값 상위 n」: 기대 종류가 yes 가 아니면 사이 값 가운데 몇 번째인가(no 이하면 None — n 으로 못 닿음)
    need_n: int | None = 0
    for k in exp:
        if vals[k] >= yes_at:
            continue
        if k not in between:
            need_n = None
            break
        need_n = max(need_n, between.index(k) + 1)
    read = kd3_read(vals, yes_at)
    return {
        "asked": [{"kind": k, "noul": round(vals[k], 4), "rank": rank[k], "top1": rank[k] == 1,
                   "yes": vals[k] >= yes_at} for k in exp],
        "unasked_yes": sum(vals[k] >= yes_at for k in unasked),
        "unasked_between": sum(no_at < vals[k] < yes_at for k in unasked),
        "between_total": len(between),
        "top": order[0],
        "kd3_covers": all(k in read for k in exp),
        "kd3_extra": sorted(read - set(exp)),
        "need_between_n": need_n,
    }


async def run(items: list[dict], modes: list[str], yes_at: float, no_at: float) -> list[dict]:
    s = load_settings()
    jev = Jev(make_client(s))
    out = []
    for mode in modes:
        qs = build_questions(mode)
        for it in items:
            rec = {"id": it["id"], "group": it["group"], "shape": it["shape"], "expected": it["expected"],
                   "mode": mode}
            try:
                a = await jev.ask(build_state(it, mode), qs)
            except JevError as e:
                rec["error"] = str(e)
                out.append(rec)
                continue
            vals = {k: float(a.nouls[f"kind_{k}"]) for k in KIND_CODES}
            rec.update({"ms": a.ms, "model": a.model, "nouls": {k: round(v, 4) for k, v in vals.items()},
                        **score(it, vals, yes_at, no_at)})
            out.append(rec)
            print(f"  {mode} {it['id']} {a.ms:.0f}ms", flush=True)
    return out


def table(results: list[dict]) -> str:
    lines = ["| id | 꼴 | 기대 종류 | 판 | 물은 종류 Noul(순위 · 1위 · yes) | 묻지 않은 yes | 묻지 않은 사이 | 1위 | KD3 덮음 | 사이 상위 n | ms |",
             "|---|---|---|---|---|---:|---:|---|:-:|---:|---:|"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['id']} | {r['shape']} | {', '.join(r['expected'])} | {r['mode']} | 오류: {r['error'][:60]} |||||||")
            continue
        asked = " · ".join(f"{a['kind']} {a['noul']:.2f}(#{a['rank']}{' 1위' if a['top1'] else ''}"
                           f"{' yes' if a['yes'] else ''})" for a in r["asked"])
        n = "—" if r["need_between_n"] is None else str(r["need_between_n"])
        lines.append(f"| {r['id']} | {r['shape']} | {', '.join(r['expected'])} | {r['mode']} | {asked} | "
                     f"{r['unasked_yes']} | {r['unasked_between']} | {r['top']} | {'✓' if r['kd3_covers'] else '✗'} | "
                     f"{n} | {r['ms']:.0f} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.experiments.kd8_kind_check")
    ap.add_argument("--set", required=True, type=Path, help="로컬 질문 셋 JSON(gitignore 자리)")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only", default="", help="문항 id 쉼표 목록(기본: 전부)")
    ap.add_argument("--name", action="append", default=[], help="kind=영어 이름 — §3-2a 이름 바꿔 보기")
    ap.add_argument("--inline", action="store_true", help="종류 이름을 instructions 에 직접")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    items = json.loads(args.set.read_text(encoding="utf-8"))
    if args.only:
        keep = set(args.only.split(","))
        items = [it for it in items if it["id"] in keep]
    for kv in args.name:
        k, _, v = kv.partition("=")
        assert k in RANGE_OF and v, kv
        NAME_OVERRIDE[k] = v
    INLINE["on"] = args.inline
    modes = [m for m in args.modes.split(",") if m]
    assert all(m in MODES for m in modes), modes
    for it in items:
        assert all(k in RANGE_OF for k in it["expected"]), it["id"]
    s = load_settings()
    if args.dry_run:
        print(json.dumps({"state": build_state(items[0], modes[-1]),
                          "questions": {k: v.instructions for k, v in list(build_questions(modes[-1]).items())[:2]},
                          "n_questions": len(KIND_CODES), "requests": len(items) * len(modes)},
                         ensure_ascii=False, indent=1))
        return
    if not s.has_typesafe_key:
        raise SystemExit("TYPESAFE_API_KEY 없음 — 못 돌림")
    results = asyncio.run(run(items, modes, s.jev_noul_yes_at, s.jev_noul_no_at))
    out = args.out or args.set.parent / f"results-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(table(results))
    print(f"\n결과: {out}")


if __name__ == "__main__":
    main()
