"""사람이 읽는 답 키 표(답 키 고치기 1) — `data/eval-set/key-table.md`(gitignore 로컬).

열: id · 질문 · 기대 대상(이름) · 기대 정보(한국어 — 채운 층이 자기가 아니면 괄호로 하위 / 윗층만 / 없음) · 기대 결과 · 행 ·
바뀜(앞 판 키 `_v051/` 대비). 질문 문장은 `QUESTIONS_CSV` 에서 읽어 이 로컬 파일에만 쓴다 — 코드에는 문항 내용이 없다.

    uv run python -m clinic_chat.evalset.key_table [--prev _v051]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clinic_chat.config import Settings, load_settings
from clinic_chat.evalset.loader import read_questions_csv, set_dir
from clinic_chat.walk.kinds import KIND_CODES

KIND_KO = {"overview": "개요", "mechanism": "원리", "indication": "어디에 쓰나", "outcome": "효과", "lineup": "하위 목록",
           "adverse": "부작용", "recovery": "회복", "contraindication": "금기", "avoid": "같이 하면 안 됨",
           "combo": "같이 하면 좋음", "package": "패키지", "same_day": "같은 날", "order_interval": "순서·간격",
           "offer": "상품·가격"}
LAYER_KO = {"self": "", "lower": "하위", "inherited": "윗층만", None: "없음"}
TABLE_KO = {"procedure": "시술", "procedure_case": "케이스", "product": "제품", "modality": "계열", "concern": "고민"}
FIELD_KO = {"onset_text": "발현", "duration_text": "지속"}
STATE_NOTE = {"원천 미확정": "자료에 없음 / 원장 확인 필요를 밝히면 정답", "근거 충족": "답함",
              "상태 안 맞음": "⚠ 기대 종류가 어느 층에도 없음 — 확인 필요"}


def _load(p: Path) -> dict | None:
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


ENTRY_NAMES: dict[str, str] = {}  # gold `g_entry` 이름(있으면) — 명세 대상 밖 노드(허브가 낸 윗고민 `inherited_via` 등)의 이름


def _entry_names(s: Settings) -> dict[str, str]:
    import csv

    p = s.path(s.gold_csv_dir)
    f = p / "g_entry.csv" if p else None
    if not f or not f.is_file():
        return {}
    with f.open(encoding="utf-8-sig", newline="") as fh:
        return {f"{r['entity_table']}:{r['entity_code']}": r["entity_name"] for r in csv.DictReader(fh)}


def _names(spec: dict) -> dict[str, str]:
    return {**ENTRY_NAMES,
            **{f"{e['table']}:{e['code']}": e.get("name") or e["code"] for e in spec.get("entries") or []}}


def asked_entries(spec: dict) -> list[dict]:
    return [e for e in spec.get("entries") or [] if not e.get("upper_of") and not e.get("lower_of")]


def target_cell(spec: dict) -> str:
    out = []
    for e in asked_entries(spec):
        out.append(e.get("name") or e["code"])
    for e in spec.get("entries") or []:
        if e.get("upper_of"):
            out.append(f"(윗층 기준: {e.get('name') or e['code']})")
    low = [f"{e.get('name') or e['code']}({TABLE_KO.get(e['table'], e['table'])})"
           for e in spec.get("entries") or [] if e.get("lower_of")]
    if low:
        out.append(f"(하위 기준: {' · '.join(low)})")
    return " · ".join(out) or "—"


def kinds_cell(spec: dict, key: dict) -> str:
    names = _names(spec)
    met = key.get("kinds_met") or []
    multi = len({m["target"] for m in met}) > 1
    parts = []
    for k in [k for k in KIND_CODES if k in (key.get("expected_kinds") or [])]:
        ms = [m for m in met if m["kind"] == k]
        if not ms:
            parts.append(f"{KIND_KO[k]}(조회 없음)")
            continue
        notes = []
        for m in ms:
            lab = LAYER_KO.get(m.get("met_by"), str(m.get("met_by")))
            if m.get("inherited_via") and m.get("met_by") == "inherited":
                lab += " — " + " · ".join(names.get(v, v) for v in m["inherited_via"]) + " 기준"
            if lab or multi:
                notes.append(f"{names.get(m['target'], m['target'])} {lab or '자기'}".strip() if multi else lab)
        parts.append(KIND_KO[k] + (f"({' / '.join(notes)})" if notes else ""))
    return " · ".join(parts) or "—"


def result_cell(key: dict, spec: dict | None = None) -> str:
    st = key.get("expected_end_state") or ""
    out = f"{st} — {STATE_NOTE.get(st, '')}".rstrip(" —")
    asked = (spec or {}).get("asked_fields") or []
    if asked and key.get("expected_end_state_by") == "(다)":
        out += " · 물은 칸 " + " · ".join(FIELD_KO.get(f.split(".", 1)[1], f) for f in asked) + " 은 자료에 빈칸(그 밖 행은 답함)"
    return out


def row_of(spec: dict, key: dict, q: str) -> dict:
    return {"id": key["id"], "q": q, "target": target_cell(spec), "kinds": kinds_cell(spec, key),
            "result": result_cell(key, spec), "rows": len(key.get("rows") or [])}


def write_table(s: Settings, prev: str = "_v052a") -> Path:
    d = set_dir(s)
    qcsv = read_questions_csv(s)
    ENTRY_NAMES.clear()
    ENTRY_NAMES.update(_entry_names(s))
    lines = ["# 답 키 표 — 사람이 읽는 꼴(로컬 · gitignore)", "",
             "기대 정보의 괄호 = 그 정보를 채운 층이 물은 대상 자신이 아닐 때 — **하위**(하위 제품 · 케이스 · 시술의 것으로 밝힘) · "
             "**윗층만**(물려받은 윗층 값 — 층을 밝히면 정답) · **없음**(어느 층에도 없음). "
             "「윗층 기준 · 하위 기준」 대상은 키 전용(허브가 아직 펴지 않는 자리를 키가 정답 꼴로 메운 것 — B · C 에 주지 않는다).",
             f"「바뀜」 = 앞 판 키(`{prev}/`) 대비 바뀐 칸.", "",
             "| id | 질문 | 기대 대상 | 기대 정보 | 기대 결과 | 행 | 바뀜 |", "|---|---|---|---|---|---:|---|"]
    for p in sorted((d / "calls").glob("Q*.json")):
        spec = _load(p) or {}
        key = _load(d / "answer-key" / p.name)
        if not key:
            continue
        r = row_of(spec, key, ((qcsv.get(spec["id"]) or {}).get("question") or "").replace("|", "／"))
        ospec, okey = _load(d / prev / "calls" / p.name), _load(d / prev / "answer-key" / p.name)
        ch = []
        if ospec and okey:
            o = row_of(ospec, okey, r["q"])
            ch = [lab for lab, c in (("대상", "target"), ("정보", "kinds"), ("결과", "result"), ("행", "rows")) if o[c] != r[c]]
            if "행" in ch:
                ch[ch.index("행")] = f"행 {o['rows']}→{r['rows']}"
        mark = ("▲ " + " · ".join(ch)) if ch else ""
        lines.append(f"| {r['id']} | {r['q']} | {r['target']} | {r['kinds']} | {r['result']} | {r['rows']} | {mark} |")
    out = d / "key-table.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.evalset.key_table")
    ap.add_argument("--prev", default="_v052a")
    a = ap.parse_args()
    print(json.dumps({"table": str(write_table(load_settings(), a.prev))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
