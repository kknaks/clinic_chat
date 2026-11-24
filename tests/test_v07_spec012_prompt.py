"""SPEC-012 0.1.2 WP-3 — 답 글 규칙(HD1 ~ HD8) 원칙 문구가 있고, 대본 문장 · 금지 낱말 목록은 없다(문구만 봄 · LLM 0)."""

from __future__ import annotations

import json
from pathlib import Path

P = json.loads((Path(__file__).resolve().parents[1] / "prompts" / "consult.json").read_text(encoding="utf-8"))
ALL = json.dumps(P, ensure_ascii=False)
KOS = P["kos"]["rules"]


def _rule(prefix: str) -> str:
    return next(r for r in KOS if r.startswith(prefix))


def test_hd1_same_day_only_when_asked_no_script():
    assert "같은 날은 따로 확인이 필요해요" not in ALL  # H11 대본 문장 없음
    assert ALL.count("물었을 때만") >= 2  # system · system_v3 두 곳


def test_hd2_hd4_hd6_principles():
    assert "자료 사정은 말하지 않는다" in ALL  # HD2
    assert "이미 앞 답에서 한 설명은 다시 하지 않는다" in ALL  # HD4
    assert "돌려 말하는 한 줄로만 답을 끝내지 않는다" in ALL and "묻지 않은 안전 줄은 먼저 꺼내지 않는다" in ALL  # HD6


def test_hd3_hd7_hd8_r16_r21():
    assert "병원 사실(명동점에 그 진료항목이 있는지)로만" in _rule("R16.")  # HD3
    assert "names_only" in _rule("R17.")  # HN2
    assert "list_held" in _rule("R18.") and "out_pick" in _rule("R19.") and "asked_pick" in _rule("R19.")
    assert "빠짐없이" in _rule("R20.")  # HD7
    assert "axis" in _rule("R21.")  # HD8


def test_hd5_pick_rule_unknown_procedure_out():
    pick = " ".join(P["pick"]["rules"])
    assert "목록 어디에도 없는 시술 이름" in pick and "되묻지 않는다" in pick


# 검수 P1-1 — 채점(quality/score.py 기준 10 · 11) 금지 문구를 프롬프트에 예문으로 박지 않는다(원칙만)
SCORE_PATTERNS = (r"값이\s?(같이|둘\s?다|따로)", r"적혀\s?있", r"설명도\s?있고", r"드리기\s?어려", r"모르겠", r"새로\s?가져온",
                  r"나와\s?있지\s?않")
SPEC012_LINES = ("물었을 때만", "자료 사정은 말하지 않는다", "이미 앞 답에서 한 설명은", "돌려 말하는 한 줄로만",
                 "돌려 막는 한 줄로 끝내지 않음", "그 부분만 상담 때", "R17.", "R18.", "R19.", "R20.", "R21.", "R22.", "R23.", "R24.",
                 "목록 어디에도 없는 시술 이름")


def test_p1_1_no_scoring_phrases_in_prompt():
    import re

    for pat in SCORE_PATTERNS:
        assert not re.search(pat, ALL), pat
    lines = [x for x in ALL.split('"') if any(k in x for k in SPEC012_LINES)]
    lines.append(_rule("R16.").split("목록은 화면에 있다.")[1])  # R16 은 이 스펙이 더한 뒷문장만(앞 따옴은 옛 규칙)
    assert len(lines) >= 10
    for x in lines:  # SPEC-012 가 쓴 줄은 예문 따옴(「…」) 없이 원칙만(데이터 값 「가능」 은 예외 — same_day 칸 값)
        quoted = [q for q in re.findall(r"「([^」]*)」", x) if q != "가능"]
        assert not quoted, (x[:60], quoted)
        assert not re.search(r"확인이\s?필요", x), x[:60]  # 옛 0.2 system 줄(원천 미확정 설명)은 이 스펙 밖
