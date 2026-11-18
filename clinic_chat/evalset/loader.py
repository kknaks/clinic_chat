"""세트 로더(WORK-002 §8 6-a) — `EVAL_SET_DIR/set.jsonl` · 37 문항의 `question` 은 `QUESTIONS_CSV` 에서 `id` 로 채운다.

세트 파일 · 질문 문장 · 모범 답은 gitignore 로컬에만 있다. 이 코드에는 문항 내용이 없다.

    uv run python -m clinic_chat.evalset.loader --validate
    uv run python -m clinic_chat.evalset.loader --confirm N01,N02 --by <확인한 사람>   # draft → confirmed (W-8)
    uv run python -m clinic_chat.evalset.loader --fill-entries   # 37 의 빈 expected_entries ← calls/<id>.json entries
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from clinic_chat.config import Settings

SET_FILE = "set.jsonl"
FIELDS = ("id", "bundle", "range", "question", "expected_end_state", "expected_entries", "expected_kinds",
          "expected_scope_gap", "answer_key", "ledger_diff", "source", "model_answer", "two_readings", "status")
# 선택 칸(0.5.2 — 다른 줄에는 없다 · 꼴만 검증): 키 전용 기준 대상 둘 · 답 키 검토 기록 · 대상별 기대 종류(WORK-008 §11 B-1
# 코디 결정 11-17 — 있으면 그 대상은 그 종류만 · 없으면 `expected_kinds[]` · 채점 기준(키)만 바뀌고 B · C 는 안 바뀐다)
OPTIONAL_FIELDS = ("key_upper_entries", "key_lower_entries", "key_fix1", "expected_kinds_by_entry")
# SPEC-002 §9 표 — 묶음(source)별 문항 수(0.5.0 — 길 찾기 16 · 합 85). 「구멍」(§9-3d · 10)은 문장이 확정돼 줄이 설 때 올린다
BUNDLE_COUNTS = {"37": 37, "추가": 19, "범위 밖": 4, "속성 시작": 4, "이름 없음": 5, "길 찾기": 16}
HOLES_BUNDLE = "구멍"  # WORK-008 WP-4 — draft 꼴(`pick --holes`)의 묶음 이름 · 수는 아직 BUNDLE_COUNTS 밖


def kinds_of_entry(obj: dict, node: str, of: str | None = None) -> list[str]:
    """한 대상의 기대 종류 — `expected_kinds_by_entry{표:코드: kinds[]}` 에 그 대상(키 전용 기준 대상은 `of` 대상)이 있으면
    그 종류만, 없으면 문항의 `expected_kinds[]`. `obj` = 세트 줄 · 명세 · 키(같은 칸 이름)."""
    by = obj.get("expected_kinds_by_entry") or {}
    if node in by:
        return list(by[node])
    if of and of in by:
        return list(by[of])
    return list(obj.get("expected_kinds") or [])


def _check_optional(x: dict, kind_codes) -> list[str]:
    errs, xid = [], x.get("id")
    exp = set(x.get("expected_entries") or [])
    node_ok = lambda v: isinstance(v, str) and v.count(":") == 1 and all(v.split(":"))  # noqa: E731
    for f in ("key_upper_entries", "key_lower_entries"):
        if f not in x:
            continue
        if not isinstance(x[f], list):
            errs.append(f"{xid}: {f} 는 목록")
            continue
        for u in x[f]:
            if not isinstance(u, dict) or not node_ok(u.get("node")) or not node_ok(u.get("of")):
                errs.append(f"{xid}: {f} 꼴 {{node, of}} = 표:코드 아님 {u!r}")
            elif u["of"] not in exp:
                errs.append(f"{xid}: {f} of {u['of']} 가 expected_entries 밖")
    if "key_fix1" in x and not (isinstance(x["key_fix1"], dict) and x["key_fix1"].get("at") and x["key_fix1"].get("why")):
        errs.append(f"{xid}: key_fix1 꼴 {{at, why}} 아님")
    if "expected_kinds_by_entry" in x:
        by = x["expected_kinds_by_entry"]
        if not isinstance(by, dict):
            errs.append(f"{xid}: expected_kinds_by_entry 는 {{표:코드: kinds[]}}")
        else:
            for node, ks in by.items():
                if node not in exp:
                    errs.append(f"{xid}: expected_kinds_by_entry {node} 가 expected_entries 밖")
                if not isinstance(ks, list) or not ks or [k for k in ks if k not in kind_codes]:
                    errs.append(f"{xid}: expected_kinds_by_entry {node} 종류 꼴 {ks!r}")
    return errs


def set_dir(s: Settings) -> Path:
    p = s.path(s.eval_set_dir)
    assert p is not None
    return p


def read_questions_csv(s: Settings) -> dict[str, dict]:
    p = s.path(s.questions_csv)
    if not p or not p.is_file():
        return {}
    with p.open(encoding="utf-8-sig", newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def read_set_lines(s: Settings) -> list[dict]:
    p = set_dir(s) / SET_FILE
    if not p.is_file():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def load_set(s: Settings) -> dict[str, dict]:
    qs = read_questions_csv(s)
    out: dict[str, dict] = {}
    for item in read_set_lines(s):
        it = dict(item)
        if it.get("source") == "37" and not it.get("question"):
            it["question"] = (qs.get(it["id"]) or {}).get("question")
        if not it.get("expected_entries"):  # 보고서 「대상 일치」 의 기대 — 세트 칸이 비면 명세 entries(W-3 · 칸은 안 고침)
            sp = set_dir(s) / "calls" / f"{it['id']}.json"
            if sp.is_file():
                it["spec_entries"] = spec_entries(json.loads(sp.read_text(encoding="utf-8")))
        out[it["id"]] = it
    return out


def validate(lines: list[dict]) -> list[str]:
    """칸 검증 — 85 줄 · id 유일 · 묶음별 수 = SPEC §9 표 · status 값 · `expected_kinds` 는 §3-2a 코드."""
    from clinic_chat.walk.kinds import KIND_CODES

    errs = []
    ids = [x.get("id") for x in lines]
    if len(lines) != sum(BUNDLE_COUNTS.values()):
        errs.append(f"줄 수 {len(lines)} != {sum(BUNDLE_COUNTS.values())}")
    if len(set(ids)) != len(ids):
        errs.append("id 중복")
    for b, n in BUNDLE_COUNTS.items():
        got = sum(1 for x in lines if x.get("source") == b)
        if got != n:
            errs.append(f"묶음 {b}: {got} != {n}")
    for x in lines:
        miss = [f for f in FIELDS if f not in x]
        if miss:
            errs.append(f"{x.get('id')}: 칸 없음 {miss}")
        if x.get("status") not in ("draft", "confirmed"):
            errs.append(f"{x.get('id')}: status {x.get('status')!r}")
        bad_k = [k for k in x.get("expected_kinds") or [] if k not in KIND_CODES]
        if bad_k:
            errs.append(f"{x.get('id')}: expected_kinds 표 밖 {bad_k}")
        if x.get("source") == "37" and x.get("question"):
            errs.append(f"{x.get('id')}: 37 문항은 질문 문장을 복사하지 않는다")
        errs.extend(_check_optional(x, KIND_CODES))
    return errs


def write_set_lines(s: Settings, lines: list[dict]) -> None:
    p = set_dir(s) / SET_FILE
    p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")


def spec_entries(spec: dict) -> list[str]:
    """명세 `calls/<id>.json` 의 `entries` → 기대 대상 `표:코드`(상품 이름 길 `offer_name` · 키 전용 윗층 기준 대상
    `upper_of` · 하위 기준 대상 `lower_of` 는 대상이 아니라 뺀다)."""
    return [f"{e['table']}:{e['code']}" for e in spec.get("entries") or []
            if e.get("code") and e.get("table") != "offer_name" and not e.get("upper_of") and not e.get("lower_of")]


def fill_expected_entries(s: Settings, sources: tuple[str, ...] = ("37",)) -> dict:
    """세트 칸 `expected_entries[]` 가 빈 문항을 명세 `calls/<id>.json` 의 `entries` 로 채운다(결정론 · 같은 값 — 코드 검수
    v05 W-3). 이미 값이 있는 문항 · 명세가 없는 문항 · `sources` 밖 묶음은 건드리지 않는다. 줄 순서 · 다른 칸 그대로."""
    lines = read_set_lines(s)
    calls = set_dir(s) / "calls"
    filled, no_spec, empty_spec = [], [], []
    for x in lines:
        if x.get("source") not in sources or x.get("expected_entries"):
            continue
        p = calls / f"{x['id']}.json"
        if not p.is_file():
            no_spec.append(x["id"])
            continue
        ents = spec_entries(json.loads(p.read_text(encoding="utf-8")))
        if not ents:
            empty_spec.append(x["id"])
            continue
        x["expected_entries"] = ents
        filled.append(x["id"])
    if filled:
        write_set_lines(s, lines)
    return {"filled": len(filled), "no_spec": no_spec, "empty_spec": empty_spec,
            "still_empty": [x["id"] for x in lines if not x.get("expected_entries")]}


def confirm_items(s: Settings, ids: list[str], by: str) -> dict:
    """사용자가 확정한 문항을 `status: confirmed` + `confirmed{by, at}` 로(줄 순서 · 다른 칸 그대로). 모르는 id 는 그대로 알린다."""
    lines = read_set_lines(s)
    at = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    known = {x.get("id") for x in lines}
    done = []
    for x in lines:
        if x.get("id") in ids and x.get("status") != "confirmed":
            x["status"] = "confirmed"
            x["confirmed"] = {"by": by, "at": at}
            done.append(x["id"])
    write_set_lines(s, lines)
    return {"confirmed": done, "unknown": [i for i in ids if i not in known],
            "already": [i for i in ids if i in known and i not in done]}


def main() -> None:
    from clinic_chat.config import load_settings

    ap = argparse.ArgumentParser(prog="clinic_chat.evalset.loader")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--confirm")
    ap.add_argument("--by")
    ap.add_argument("--fill-entries", action="store_true", help="37 의 빈 expected_entries 를 명세 entries 로 채움")
    a = ap.parse_args()
    s = load_settings()
    if a.fill_entries:
        print(json.dumps(fill_expected_entries(s), ensure_ascii=False))
        return
    if a.confirm:
        if not a.by:
            ap.error("--confirm 에는 --by 가 필요하다")
        print(json.dumps(confirm_items(s, [x for x in a.confirm.split(",") if x], a.by), ensure_ascii=False))
        return
    lines = read_set_lines(s)
    errs = validate(lines)
    print(json.dumps({"lines": len(lines), "draft": sum(1 for x in lines if x.get("status") == "draft"),
                      "errors": errs}, ensure_ascii=False))


if __name__ == "__main__":
    main()
