"""「원장 오류 따라감」 재료(SPEC-002 §8-4 · O-10 (a)) — LCK 글 칸은 채점 단계에서만 `LEDGER_CHECK_PATH` 를 직접 읽는다.

문항 ↔ LCK = 세트 파일 `ledger_diff`(코드 맞춤 — 사용자 확인) · 없으면 답 키 `ledger_check[]` 의 id.
글 칸은 로컬 채점 입력 · 기록에만 남고 보고서에는 id 만 싣는다.
"""

from __future__ import annotations

import csv
from pathlib import Path

TEXT_COLS = ("source", "pointer", "silver_now")


def load(path: str | Path | None) -> dict[str, dict]:
    if not path or not Path(path).is_file():
        return {}
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return {r["id"]: {c: r.get(c) for c in TEXT_COLS} for r in csv.DictReader(f)}


def lck_ids(item: dict | None, key: dict) -> list[str]:
    ids = list((item or {}).get("ledger_diff") or [])
    if not ids:
        ids = [i for x in key.get("ledger_check") or [] for i in x.get("lck_ids") or []]
    return sorted(dict.fromkeys(ids))


def rows_for(table: dict[str, dict], ids: list[str]) -> list[dict]:
    return [{"id": i, **table[i]} for i in ids if i in table]
