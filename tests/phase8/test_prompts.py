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


TOOLS11 = ("resolve", "recommend", "procedure_card", "pair_check", "check_conditions", "compare", "combos", "neighbors",
           "vocab", "peek", "expand")


def test_tool_names_filled_per_provider():
    """§3-8 막는 법 1 — claude-cli 는 `mcp__hub__` 이름 · sdk · codex 는 짧은 이름 · 채운 글 sha 가 provider 마다."""
    import re

    c = prompts.system_for("case2", "claude-cli")
    assert "{tool:" not in c
    named = set(re.findall(r"mcp__hub__\w+", c))
    assert named == {f"mcp__hub__{t}" for t in TOOLS11}  # ⊆ 세션 도구 목록(system/init.tools = mcp__hub__ 11)
    assert "도구 설명 · 응답에 나오는 짧은 이름(resolve · expand 등)은 위 이름의 도구를 가리킨다" in c
    sdk = prompts.system_for("case2", "sdk")
    assert "mcp__hub__" not in sdk and "{tool:" not in sdk and "`resolve` 로" in sdk
    assert prompts.system_for("case2", "codex-cli") == sdk  # S-3 전 — 짧은 이름 그대로
    assert "`fact_ids` 로 받은 뒤 인용" in sdk
    v = {p: prompts.versions(p)["case2"] for p in ("claude-cli", "sdk", "codex-cli")}
    assert v["claude-cli"] != v["sdk"] == v["codex-cli"]
    assert prompts.versions("claude-cli")["case1"] == prompts.version("case1")  # 자리표 없는 글은 파일 sha


def test_case3_prompt_walk_lines():
    s = prompts.system_for("case3", "claude-cli")
    assert "`quotable: false` 줄은 근거로 인용하지 않는다" in s and "rank 순" in s and "출처 노드" in s
