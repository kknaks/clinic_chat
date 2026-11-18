"""② LLM 채점(Opus · 가리기 — SPEC-002 §8-3 · O-8 바꿈).

- 입력 = 질문 + 답(인용을 `[근거 n]` 으로 가림) + 전체 답 키(행 값) + `expected_end_state` + `ledger_check[]` + LCK 글 +
  `questions.csv answer`(「잘린 표본 요약」 이라고 밝혀). **열 이름 · 도구 흔적 · 속도 · 다른 열 답 · B 의 end_state 없음.**
- 항목 5 · 통과 / 실패 / 해당 없음 · 실패 근거(`gid` + 칸) 없으면 그 항목 무효.
"""

from __future__ import annotations

import json
import re

from clinic_chat import prompts
from clinic_chat.llm import cli
from clinic_chat.llm.cli import CliSpec

ITEMS = ("accuracy", "safety", "uncertainty", "traceability", "asked_only")
VERDICTS = ("pass", "fail", "na")
GID_RE = re.compile(r"\bG[A-Z]{2}-\d{5}\b")
from clinic_chat.columns.base import RAW_CITE_RE  # noqa: E402

GID_BRACKET_RE = re.compile(r"\[\s*G[A-Z]{2}-\d{5}\s*\]")

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": [*ITEMS, "graded_end_state", "followed_ledger_error"],
    "properties": {
        **{k: {"type": "object", "additionalProperties": False, "required": ["verdict", "evidence"],
               "properties": {"verdict": {"type": "string", "enum": list(VERDICTS)},
                              "evidence": {"type": "array", "items": {
                                  "type": "object", "additionalProperties": False, "required": ["gid", "field", "why"],
                                  "properties": {"gid": {"type": "string"}, "field": {"type": "string"},
                                                 "why": {"type": "string"}}}}}} for k in ITEMS},
        "graded_end_state": {"type": "string",
                             "enum": ["근거 충족", "후보 소진", "원천 미확정", "실행 중단", "범위 밖", "입구 없음"]},
        "followed_ledger_error": {"type": "boolean"},
    },
}


def mask(answer: str) -> tuple[str, dict[str, str]]:
    """인용 꼴(gid · 원장 포인터)을 중립 표지로 — 열이 드러나지 않게."""
    table: dict[str, str] = {}

    def sub(m: re.Match) -> str:
        k = m.group(0)
        if k not in table:
            table[k] = f"[근거 {len(table) + 1}]"
        return table[k]

    out = GID_BRACKET_RE.sub(sub, answer or "")
    out = RAW_CITE_RE.sub(sub, out)
    out = GID_RE.sub(sub, out)
    return out, table


def judge_input(*, question: str, answer: str, key_rows: list[dict], expected_end_state: str, ledger_check: list,
                lck_text: list[dict], sample_answer: str | None, d_results: dict | None, two_readings: bool,
                scope_gap: bool, kinds_met: list[dict] | None = None) -> str:
    """`kinds_met` = 키의 기대 종류마다 채운 층(RQ1 — 답 키 고치기 1 K3): `inherited` 로만 채운 종류는 **윗층 값으로 답하고
    층을 밝히면 정답**이다 — 채점자가 그 종류를 「빠짐」 · 「층 섞기」 로 떨어뜨리지 않게 함께 준다."""
    masked, _ = mask(answer)
    parts = ["## 질문", question, "", "## 채점할 답", masked, "",
             "## 전체 답 키(행 값)", "\n".join(json.dumps(r, ensure_ascii=False) for r in key_rows), "",
             "## 기대 종료 상태", expected_end_state, "",
             "## 원장 확인 항목", json.dumps(ledger_check, ensure_ascii=False), json.dumps(lck_text, ensure_ascii=False), "",
             "## 문항 표지", json.dumps({"two_readings": two_readings, "scope_gap": scope_gap}, ensure_ascii=False)]
    if kinds_met:
        parts += ["", "## 기대 정보 종류 · 채운 층(inherited = 윗층 값으로 채움 — 층을 밝혔으면 정답 · lower = 하위의 것으로 밝힘)",
                  json.dumps([{k: m.get(k) for k in ("target", "kind", "met_by", "inherited_via") if m.get(k)}
                              for m in kinds_met], ensure_ascii=False)]
    if sample_answer:
        parts += ["", "## 참고 — 잘린 표본 요약(답 키 아님)", sample_answer]
    if d_results:
        parts += ["", "## 결정론 검사 결과(근거 추적 참고)", json.dumps(d_results, ensure_ascii=False)]
    return "\n".join(parts)


def validate(structured: dict | None) -> dict:
    """항목 5 가 왔나 · 실패에 근거가 있나(없으면 그 항목 invalid)."""
    if not isinstance(structured, dict):
        return {"ok": False, "items": {}, "error": "구조화 출력 없음"}
    items = {}
    for k in ITEMS:
        v = structured.get(k) or {}
        verdict = v.get("verdict")
        ev = v.get("evidence") or []
        if verdict not in VERDICTS:
            items[k] = {"verdict": "invalid", "reason": "판정 없음"}
        elif verdict == "fail" and not any(e.get("gid") and e.get("field") for e in ev):
            items[k] = {"verdict": "invalid", "reason": "실패 근거 없음", "evidence": ev}
        else:
            items[k] = {"verdict": verdict, "evidence": ev}
    return {"ok": all(k in structured for k in ITEMS), "items": items,
            "graded_end_state": structured.get("graded_end_state"),
            "followed_ledger_error": bool(structured.get("followed_ledger_error"))}


async def grade_one(spec: CliSpec, text: str, max_concurrent: int = 1) -> dict:
    r = await cli.run(spec, system=prompts.read("judge.md"), prompt=text, schema=SCHEMA, max_concurrent=max_concurrent)
    v = validate(r["structured"])
    v.update({"model": r["observed_model"], "error": r["error"], "ms": r["timing"].get("total_ms")})
    return v
