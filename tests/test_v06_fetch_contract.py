"""SPEC-011 WP-1 · WP-4 — 1차 이해 `fetch` 계약(JC1 ~ JC6 · LG1 · LG4) · 호출마다 토큰 기록(11-23)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from clinic_chat.config import ROOT
from clinic_chat.consult import verify as vf
from clinic_chat.consult.turn import call_tokens
from clinic_chat.consult.understand import Raw

PICK = json.loads((ROOT / "prompts" / "consult.json").read_text())["pick"]


def test_fetch_missing_is_all_default():
    f = vf.fetch_of(None)
    assert f == {"about": "targets", "kinds": [], "layers": "sub", "scope": "branch", "lineup": "none",
                 "from": "default", "fixed": []}


def test_fetch_enum_fix_and_unknown_kinds():
    f = vf.fetch_of({"about": "topic", "kinds": ["lineup", "zzz", "overview"], "scope": "everywhere",
                     "lineup": "names"})
    assert f["about"] == "topic" and f["lineup"] == "names" and f["from"] == "understand"
    assert f["scope"] == "branch" and f["kinds"] == ["overview", "lineup"]  # 걷기 `KIND_CODES` 차례 · 모르는 코드 버림
    assert set(f["fixed"]) == {"scope", "kinds"}
    assert vf.fetch_of({"layers": "self"})["layers"] == "self"  # 칸이 오면 받는다(2차는 `sub` 고정 — JC3)
    assert vf.fetch_of({})["fixed"] == ["about", "scope", "lineup"]  # 빈 칸은 기본값으로 채운 것(고침) · `layers` 는 0.6.0 에 없는 칸


def test_parsed_and_accepted_carry_fetch():
    raw = {"targets": [], "kind": "new", "fetch": {"about": "topic", "kinds": [], "scope": "all", "lineup": "none"}}
    assert vf.parsed_view(raw)["fetch"] == raw["fetch"]  # LG1
    el = SimpleNamespace(has=lambda t, c: False, name=lambda t, c: "")
    a = vf.verify(raw, el, {"concerns": [], "sites": [], "subjects": []}, first_turn=True, talk_streak=0, talk_max=2,
                  prev_row=None)
    assert a.view()["fetch"]["scope"] == "all" and a.fetch["about"] == "topic" and a.fetch["from"] == "understand"


def test_prompt_rules_20():
    rules = PICK["rules"]
    out = next(r for r in rules if r.startswith("출력 꼴:"))
    shape = json.loads(out.split(":", 1)[1].strip())
    assert set(shape["fetch"]) == {"about", "kinds", "scope", "lineup"}  # JC3 — layers 칸 없앰 · JC6 site_filter 없음
    heads = [r.split(" ", 1)[0] for r in rules if r[:2] == "20"]
    assert heads == ["20.", "20-1.", "20-2.", "20-3.", "20-4.", "20-5."]
    assert not any("scope 가 \"all\" 일 때만" in r for r in rules)  # r4 20-6 앞 절 뺌(P2-13)
    assert PICK["version"] == "0.6.0-draft"


def test_understand_usage_keeps_cache_creation():
    from clinic_chat.consult import understand as und

    src = (ROOT / "clinic_chat" / "consult" / "understand.py").read_text()
    assert '"cache_creation_input_tokens": u.get("cache_creation_input_tokens")' in src  # LG4
    assert und.Raw().usage == {}


def test_call_tokens_per_call_and_sum():
    raw = Raw(ms=900.0, usage={"input_tokens": 3, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 9000,
                               "output_tokens": 150, "thinking_tokens": None}, models=["claude-sonnet-x"])
    cs = SimpleNamespace(raw=raw)
    llm = {"tokens": {"input_tokens": 5, "output_tokens": 700, "cache_creation_input_tokens": 2000,
                      "cache_read_input_tokens": 40000}, "observed_model": "claude-sonnet-x", "timing": {"total_ms": 8000}}
    t = call_tokens(cs, llm)
    assert t["input_tokens"] == 5 and t["output_tokens"] == 700  # 옛 칸(답 호출) 그대로
    assert [c["role"] for c in t["calls"]] == ["understand", "answer"]
    u, a = t["calls"]
    assert (u["input"], u["cache_creation"], u["cache_read"], u["output"], u["ms"]) == (3, 100, 9000, 150, 900.0)
    assert u["thinking"] is None and a["thinking"] is None  # 없으면 null — 지어 넣지 않음
    assert t["sum"] == {"input": 8, "output": 850, "cache_creation": 2100, "cache_read": 49000}


def test_call_tokens_missing_values_are_null():
    t = call_tokens(SimpleNamespace(raw=Raw(usage={})), {"tokens": {}, "observed_model": None, "timing": {}})
    assert all(c[k] is None for c in t["calls"] for k in ("input", "output", "cache_creation", "cache_read"))
    assert t["sum"] == {"input": None, "output": None, "cache_creation": None, "cache_read": None}
    assert call_tokens(None, None) == {"calls": [], "sum": {"input": None, "output": None, "cache_creation": None,
                                                             "cache_read": None}}


def test_parse_self_corrected_double_object_takes_last():
    """WP-1 재생 11-23(5bce T2) — 이해 호출이 객체를 낸 뒤 「Wait —」 로 고친 객체를 하나 더 냄 → 맨 끝 객체."""
    raw = ('{"targets":[{"table":"case","code":"CAS-009","sure":false}],"kind":"new","fetch":{"about":"targets"}}\n\n'
           'Wait — table name must be exact. Let me give the corrected final answer:\n\n'
           '{"targets":[{"table":"site","code":"SITE-072","sure":true}],"kind":"new","sites":["SITE-072"],'
           '"fetch":{"about":"targets","kinds":[],"scope":"branch","lineup":"none"}}')
    d = vf.parse(raw)
    assert d["targets"][0]["table"] == "site" and d["fetch"]["scope"] == "branch"
    assert vf.parse('앞 글 {"kind":"talk"} 뒤 글') == {"kind": "talk"}


def test_parse_tail_fragment_object_is_not_taken():
    """검수 P2-1 — 객체 뒤 「Wait — scope 는 {"scope":"all"}」 같은 조각은 받지 않는다(앞 온전한 객체를 받음)."""
    raw = ('{"targets":[{"table":"procedure","code":"PRC-001","sure":true}],"kind":"new",'
           '"fetch":{"about":"targets","kinds":[],"scope":"branch","lineup":"none"}}\n\n'
           'Wait — scope 는 {"scope":"all"} 이어야 한다.')
    d = vf.parse(raw)
    assert d["targets"][0]["code"] == "PRC-001" and d["kind"] == "new"
    import pytest

    with pytest.raises(vf.UnderstandJSONError):
        vf.parse('{"a": 1 깨짐 {"scope":"all"}')
