"""WP-4 — 대화 장부(T16 · T20 · T21 · LG4 · LG5 · CL13 · W-3) — 줄을 손으로 적은 꼴."""

from __future__ import annotations

import json
from pathlib import Path

from clinic_chat.consult.ledger import TURNS_FILE, Ledger, blocked


def end_row(n, *, error=None, answered=True, new_lines=(), next_n=1, state="open", sid="S1", gh="h1", conv_end=None):
    return {"turn": n, "question": f"q{n}", "llm": {"session_id": sid, "answered": answered}, "session_state": state,
            "session_next": {"session_id": sid}, "targets": [], "material": {"new_lines": list(new_lines),
                                                                          "seen_lines": [], "next_n": next_n},
            "answer": "a", "timing": {}, "build": {"gold_hash": gh}, "error": error, "conv_end": conv_end}


def test_two_lines_per_turn_and_reload(tmp_path):
    led = Ledger(tmp_path)
    c = led.new_conv(None)
    s = led.start(c, "q1")
    assert s["phase"] == "start" and c.running == 1
    led.end(c, end_row(1, new_lines=[{"n": 1, "gid": "G1"}, {"n": 2, "summary": True, "key": "K", "gids": ["G2"]}],
                       next_n=3))
    rows = [json.loads(x) for x in (tmp_path / TURNS_FILE).read_text(encoding="utf-8").splitlines()]
    assert [r["phase"] for r in rows] == ["start", "end"]
    c2 = Ledger(tmp_path).get(c.conv_id)
    assert c2.chat.state == "open" and c2.chat.session_id == "S1" and c2.next_n == 3
    assert c2.sent == {"G1": 1} and c2.summaries == {"K": 2} and c2.gold_hash == "h1"  # 요약 gid 는 「실은 gid」 아님


def test_t20_start_only_is_broken(tmp_path):
    led = Ledger(tmp_path)
    c = led.new_conv(None)
    led.start(c, "q1")
    c2 = Ledger(tmp_path).get(c.conv_id)
    assert c2.chat.state == "broken" and blocked(c2, 20) == "broken"


def test_t21_error_turn_not_counted(tmp_path):
    led = Ledger(tmp_path)
    c = led.new_conv(None)
    led.start(c, "q1")
    led.end(c, end_row(1, error={"kind": "exit"}, answered=False, new_lines=[{"n": 1, "gid": "G1"}], next_n=2,
                       state="new"))
    assert c.sent == {} and c.next_n == 1 and c.answered() == 0


def test_cl13_turn_limit_counts_answered(tmp_path):
    led = Ledger(tmp_path)
    c = led.new_conv(None)
    for n in (1, 2):
        led.start(c, f"q{n}")
        led.end(c, end_row(n, answered=n == 1, error=None if n == 1 else {"kind": "exit"}))
    assert c.answered() == 1 and blocked(c, 2) is None and blocked(c, 1) == "turn_limit"


def test_lg5_home_path_scrubbed(tmp_path):
    led = Ledger(tmp_path)
    c = led.new_conv(None)
    led.start(c, f"{Path.home()}/x")
    assert str(Path.home()) not in (tmp_path / TURNS_FILE).read_text(encoding="utf-8")


def test_conv_end_kept_on_reload(tmp_path):
    led = Ledger(tmp_path)
    c = led.new_conv(None)
    led.start(c, "q1")
    led.end(c, end_row(1, error={"kind": "build_changed"}, answered=False, conv_end="build_changed"))
    assert Ledger(tmp_path).get(c.conv_id).ended == "build_changed"
