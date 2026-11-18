"""WP-0 — E8-0 실측 도구의 입력 꼴 · 요약 함수(외부 호출 0)."""

from __future__ import annotations

import json

from clinic_chat.consult import e8


def test_first_turns_takes_first_turn_per_conv():
    lines = [json.dumps({"conv": f"C{i}", "turns": [{"n": 1, "text": f"말{i}"}, {"n": 2, "text": "둘"}]}) for i in range(5)]
    got = e8.first_turns(lines + [""], 3)
    assert [x["conv"] for x in got] == ["C0", "C1", "C2"] and got[0]["text"] == "말0"


def test_mode_args_and_env():
    base = ["claude", "-p"]
    assert e8.mode_args(base, "a")[-2:] == ["--settings", json.dumps({"alwaysThinkingEnabled": False})]
    assert e8.mode_args(base, "c") == base
    assert e8.mode_env("a")["MAX_THINKING_TOKENS"] == "0" and "MAX_THINKING_TOKENS" not in e8.mode_env("b")
    assert e8.mode_env("d")["CLAUDE_CODE_DISABLE_THINKING"] == "1"


def test_summary_counts_thinking_and_latency():
    rows = [{"ms": 1000, "api_ms": 500, "usage": {"thinking_tokens": 0, "cache_read_input_tokens": 0}},
            {"ms": 3000, "api_ms": 900, "usage": {"thinking_tokens": 120, "cache_read_input_tokens": 50}},
            {"ms": 2000, "api_ms": 700, "usage": {"thinking_tokens": 0, "cache_read_input_tokens": 50}},
            {"ms": None, "error": {"kind": "timeout"}, "usage": {}}]
    s = e8.summary(rows)
    assert s["calls"] == 4 and s["errors"] == 1 and s["thinking_nonzero"] == 1 and not s["thinking_all_zero"]
    assert s["ms_median"] == 2000 and s["first_ms"] == 1000 and s["first_cache_read"] == 0
    assert e8.summary(rows[:1])["thinking_all_zero"]
