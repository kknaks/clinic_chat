"""상담 0.4 시험 도우미(WORK-017) — 가짜 코드 · 가짜 이름 · 손으로 적은 꼴(규칙 계산 없음 · 외부 호출 0).

- `entry_rows()` = 허브 `resolve` 목록 꼴의 `g_entry` 행(다섯 표 + 표 밖 한 줄) — `state_fixture.CONCEPTS` 와 같은 코드.
- `out(...)` = 이해 호출 CLI(`--output-format json`)의 출력 한 줄 · `set_out(route, ...)` = 가짜 CLI 가 낼 글.
"""

from __future__ import annotations

import json
from pathlib import Path


def g(table: str, code: str, name: str, text: str | None = None, kind: str = "이름") -> dict:
    t = text or name
    return {"gold_table": "g_entry", "gid": f"GEN-{code}-{t}", "text": t, "norm": t.replace(" ", ""),
            "entity_table": table, "entity_code": code, "entity_name": name, "name_kind": kind}


ENTRY = [
    g("concern", "ZZC-1", "가짜고민"), g("concern", "ZZC-1", "가짜고민", "가짜고민별칭", "별칭"),
    g("concern", "ZZC-1", "가짜고민", "가짜고민별칭", "별칭"),  # 같은 글자 별칭은 한 번
    g("concern", "ZZC-2", "가짜윗고민"), g("concern", "ZZC-3", "가짜하위하나"), g("concern", "ZZC-4", "가짜하위둘"),
    g("concern", "ZZC-9", "가짜팔자"),
    g("site", "ZZS-1", "가짜부위"), g("site", "ZZS-2", "가짜부위둘"), g("site", "ZZS-9", "가짜팔자"),
    g("modality", "ZZM-1", "가짜계열"), g("modality", "ZZM-2", "가짜계열둘"),
    g("procedure", "ZZP-1", "가짜시술"), g("procedure", "ZZP-2", "가짜시술둘"), g("procedure", "ZZP-7", "가짜계열"),
    g("product", "ZZR-1", "가짜제품"), g("product", "ZZR-7", "가짜 계열"),
    g("adverse", "ZZA-1", "가짜사건"),  # 다섯 표 밖(LD8)
]


def entry_rows() -> list[dict]:
    return [dict(x) for x in ENTRY]


def list_route(rows: list[dict] | None = None):
    """`resolve` 길 — `text` 가 없고 `tables` 가 있으면 목록 꼴(그 표만 · 허브 순서)."""
    rs = rows if rows is not None else entry_rows()

    def fn(p: dict) -> dict:
        tables = [x for x in (p.get("tables") or "").split(",") if x]
        return {"candidates": [r for r in rs if not tables or r["entity_table"] in tables]}
    return fn


def out(targets: list[tuple] | None = None, kind: str = "new", sites: list[str] | None = None,
        withdraw: list[str] | None = None, kinds: list[str] | None = None, oos: bool = False, *,
        raw: str | None = None, thinking: int = 0, is_error: bool = False) -> str:
    """`targets` = [(table, code) | (table, code, sure)]."""
    if raw is None:
        tg = [{"table": t[0], "code": t[1], "sure": t[2] if len(t) > 2 else True} for t in targets or []]
        raw = json.dumps({"targets": tg, "kind": kind, "sites": sites or [], "withdraw": withdraw or [],
                          "kinds": kinds or [], "out": oos}, ensure_ascii=False)
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error, "result": raw,
                       "duration_api_ms": 900,
                       "usage": {"input_tokens": 3, "cache_read_input_tokens": 100, "output_tokens": 20,
                                 "output_tokens_details": {"thinking_tokens": thinking}},
                       "modelUsage": {"claude-test": {}}}, ensure_ascii=False)


def set_out(route: Path, *a, **kw) -> None:
    (route / "understand.json").write_text(out(*a, **kw), encoding="utf-8")


def set_seq(route: Path, outs: list[str]) -> None:
    (route / "understand.seq.jsonl").write_text("\n".join(outs) + "\n", encoding="utf-8")
    (route / "understand.n").write_text("0")


def set_map(route: Path, table: dict[str, str]) -> None:
    """{이번 말에 든 낱말: `out(...)`} — 가짜 CLI 가 이번 말로 출력을 고른다(키 "*" = 그 밖)."""
    (route / "understand.map.json").write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
