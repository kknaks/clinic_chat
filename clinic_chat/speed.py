"""속도 탭 값(WORK-004) — 열 결과 하나 → 카드 머리 값 · 여러 기록 → 문항 × 열 표 + 평균 · 중앙값.

열 결과에 이미 있는 값을 옮기기만 한다(새로 재지 않는다):
- 전체 ms · 첫 글자 ms = `timing.total_ms` · `timing.ttft_ms`(도구 사이 메모 포함)
- 첫 답 글자 ms = `timing.first_answer_text_ms`(열 시작 기준) · `timing.click_to_first_answer_ms`(화면 요청 기준 — 카드 맨 앞 ·
  표 첫 칸). 마지막 도구 호출 뒤 첫 글자 · B 계열은 도구가 없어 첫 글자와 같다
- 단계별 ms(Jev · 허브 · LLM) = B · B 전체는 `timing.parts`(LLM = 단계 ⑤ CLI 한 번 전체), C 는 허브 = `timing.stages` 의
  `tool:*` 합 · LLM = 전체 − 허브(CLI 시작 · 첫 글자 기다림 포함 — B 의 LLM 과 같은 셈)
- LLM 입력 = B · B 전체는 단계 ⑤ 프롬프트 바이트(`trace.llm_input_bytes`), C 는 질문 + 도구 결과 바이트 합(지시문 제외 · 같은 칸)
- 토큰 = CLI usage(입력 = input + cache_creation + cache_read) — 모든 열 `tokens.llm[]`(LLM 왕복마다 · C 는 도구 왕복 합) · 추론 = thinking 블록을 봤는지
"""

from __future__ import annotations

import statistics
from typing import Any

SPEED_COLS = ("b", "c", "b_full")
TABLE_KEYS = ("click_to_first_answer_ms", "total_ms", "first_answer_text_ms", "click_to_first_text_ms", "ttft_ms",
              "input_kb", "input_tokens", "output_tokens")


def _sum_tokens(toks: Any) -> tuple[int | None, int | None]:
    items = toks if isinstance(toks, list) else [toks] if isinstance(toks, dict) else []
    if not items:
        return None, None
    inp = out = 0
    for t in items:
        t = t or {}
        inp += sum(int(t.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        out += int(t.get("output_tokens") or 0)
    return inp, out


def _stage_sum(stages: list[dict], prefix: str) -> float:
    return round(sum((s.get("end_ms") or 0) - (s.get("start_ms") or 0) for s in stages
                     if str(s.get("name", "")).startswith(prefix) and s.get("end_ms") is not None), 1)


def card(col: str, result: dict | None, *, model: str | None = None, effort: str | None = None,
         question: str | None = None) -> dict | None:
    if not result:
        return None
    timing = result.get("timing") or {}
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    toks = result.get("tokens") or {}
    if col in ("b", "b_full"):
        parts = dict(timing.get("parts") or {})
        in_bytes = trace.get("llm_input_bytes")
        inp, out = _sum_tokens(toks.get("llm"))
        thinking = any(p.get("thinking_seen") for p in trace.get("llm_pages") or [])
    else:
        stages = timing.get("stages") or []
        hub = _stage_sum(stages, "tool:")
        total = timing.get("total_ms")
        parts = {"jev_ms": 0.0, "hub_ms": hub,
                 "llm_ms": round(total - hub, 1) if isinstance(total, int | float) else _stage_sum(stages, "llm#")}
        calls = trace.get("tool_calls") or []
        in_bytes = trace.get("llm_input_bytes")
        if not isinstance(in_bytes, int | float):  # 옛 기록(llm_input_bytes 없음)
            in_bytes = sum(int(c.get("output_bytes") or 0) for c in calls) + len((question or "").encode("utf-8"))
        inp, out = _sum_tokens(toks.get("llm") if "llm" in toks else toks)  # 새 꼴 `{"llm": [왕복…]}` · 옛 꼴 평평한 usage
        thinking = bool(trace.get("thinking_seen")) if "thinking_seen" in trace else None
    kinds = (trace.get("front") or {}).get("kinds") if col in ("b", "b_full") else None
    return {"column": col, "end_state": result.get("end_state"), "total_ms": timing.get("total_ms"),
            "click_to_first_text_ms": timing.get("click_to_first_text_ms"),
            "click_to_first_answer_ms": timing.get("click_to_first_answer_ms"),
            "first_answer_text_ms": timing.get("first_answer_text_ms"),
            "ttft_ms": timing.get("ttft_ms"), "parts": parts, "input_bytes": in_bytes,
            "input_kb": round(in_bytes / 1024, 1) if isinstance(in_bytes, int | float) else None,
            "input_tokens": inp, "output_tokens": out, "thinking": thinking, "model": model, "effort": effort,
            "error": bool(result.get("error")), "front_kinds": kinds}


def effort_of(row: dict, col: str) -> str | None:
    if col in (row.get("efforts") or {}):  # 열마다 실제로 넘긴 값(runner 기록)
        return row["efforts"][col]
    st = row.get("settings") or {}
    if col in ("b", "b_full"):
        return st.get("effort") if st.get("answer_reasoning") == "on" else st.get("lowest_effort")
    return st.get("effort")


def row_cards(row: dict) -> dict[str, dict]:
    model = (row.get("models") or {}).get("answer")
    out = {}
    for k in SPEED_COLS:
        c = card(k, (row.get("columns") or {}).get(k), model=model, effort=effort_of(row, k),
                 question=row.get("question"))
        if c:
            out[k] = c
    return out


def _stats(vals: list[float]) -> dict:
    v = [x for x in vals if isinstance(x, int | float)]
    if not v:
        return {"n": 0, "mean": None, "median": None}
    return {"n": len(v), "mean": round(statistics.fmean(v), 1), "median": round(statistics.median(v), 1)}


def table(rows: list[dict]) -> dict:
    """문항 × 열(누른 순간→첫 답 글자 ms · 전체 ms · 첫 답 글자 · 첫 글자 · 입력 KB · 출력 토큰) + 열마다 평균 · 중앙값."""
    lines = []
    cols: list[str] = []
    for r in rows:
        cs = row_cards(r)
        for k in cs:
            if k not in cols:
                cols.append(k)
        lines.append({"run_id": r.get("run_id"), "set_id": r.get("set_id"),
                      "columns": {k: {x: v.get(x) for x in TABLE_KEYS} for k, v in cs.items()}})
    cols = [k for k in SPEED_COLS if k in cols]
    agg = {k: {x: _stats([ln["columns"].get(k, {}).get(x) for ln in lines]) for x in TABLE_KEYS} for k in cols}
    return {"columns": cols, "rows": lines, "stats": agg}
