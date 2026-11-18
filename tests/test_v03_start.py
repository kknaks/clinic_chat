"""0.3 WP-0 — 설정 여섯 · 시작 줄 `consult_rev`(SPEC-006 §10-2 · FX3 · WORK-014 §6)."""

from __future__ import annotations

import json

from clinic_chat.consult.ledger import CONSULT_REV, TURNS_FILE, Ledger
from tests.conftest import make_settings


def _starts(tmp_path):
    return [r for r in map(json.loads, (tmp_path / TURNS_FILE).read_text(encoding="utf-8").splitlines())
            if r["phase"] == "start"]


def test_six_settings_defaults(tmp_path):
    s = make_settings(tmp_path)
    assert (s.chat_turn_kind, s.chat_talk_max, s.chat_alt_max, s.chat_voice_check, s.chat_debug_panel,
            s.chat_drawer_max) == ("on", 3, 3, "on", "off", 50)


def test_on_conv_start_has_rev_to_the_end(tmp_path):
    led = Ledger(tmp_path)
    c = led.new_conv(None)
    led.start(c, "q1", "on")
    led.end(c, {"turn": 1, "error": None, "llm": {"answered": True}})
    led.start(c, "q2", "off")  # 대화 값은 첫 턴 것으로 끝까지
    rows = _starts(tmp_path)
    assert [r.get("consult_rev") for r in rows] == [CONSULT_REV, CONSULT_REV] == [4, 4]
    assert Ledger(tmp_path).get(c.conv_id).consult_rev == 4


def test_off_conv_start_has_no_rev(tmp_path):
    """0.4 CP1 — 새 대화는 `off` 를 주어도 켠 상태 · rev 4(끈 상태로 시작하는 새 대화는 없다)."""
    led = Ledger(tmp_path)
    c = led.new_conv(None)
    led.start(c, "q1", "off")
    r = _starts(tmp_path)[0]
    assert r["consult_rev"] == CONSULT_REV and r["consult_state"] == "on"
    assert Ledger(tmp_path).get(c.conv_id).consult_rev == CONSULT_REV


def test_old_on_conv_without_rev_reloads_none(tmp_path):
    (tmp_path / TURNS_FILE).write_text(json.dumps({"conv_id": "old02", "turn": 1, "phase": "start", "question": "q",
                                                   "asked_at": "t", "consult_state": "on", "llm": {}}) + "\n",
                                       encoding="utf-8")
    c = Ledger(tmp_path).get("old02")
    assert c.consult_state == "on" and c.consult_rev is None
