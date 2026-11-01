"""case2 프롬프트 intent 한 줄(SPEC-006 v2 §3-6 ①) — 의도 표 12 · SPEC 문구 그대로."""

from __future__ import annotations

from clinic_chat import prompts

LINE = ("허브 도구를 부를 때마다 `intent` 를 반드시 넘긴다 · 의도 표: I-01 고민 → 추천 · I-02 시술 설명 · I-03 주의 · 사후관리 · "
        "I-04 부작용 · 합병증 · I-05 회복 · 효과 기간 · I-06 비교 · I-07 같이 · 순서 · 간격 · I-08 이전 시술 뒤 가능? · "
        "I-09 나한테 해도 되나(확인 조건) · I-10 용량 · 회차 · 제품 · I-11 추천 조합 · I-12 가격")


def test_case2_intent_line():
    s = prompts.system_for("case2")
    assert LINE in s
    for i in range(1, 13):
        assert f"I-{i:02d}" in s


def test_intent_line_only_case2():
    assert "`intent` 를 반드시 넘긴다" not in prompts.system_for("case1")
    assert "`intent` 를 반드시 넘긴다" not in prompts.system_for("case3")
