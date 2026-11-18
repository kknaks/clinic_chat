"""S8-T19 · S8-T17(rev 몫) — 끈 상태 막기 · `consult_rev` 4(SPEC-008 CP1 · CP7 · C8-8 · WORK-017 WP-10)."""

from __future__ import annotations

import inspect
import re

from clinic_chat.consult import turn as turn_mod
from clinic_chat.consult.ledger import CONSULT_REV, TURNS_FILE, Ledger, blocked
from clinic_chat.consult.render import TEMPLATES
from tests.conftest import make_settings
from tests.state_fixture import Run
from tests.test_v03_fixes import _old_rows, _w
from tests.v04_fixture import set_out


def test_s8_t19_default_on_and_off_setting_still_opens_on(tmp_path, fake_cli):
    assert make_settings(tmp_path).chat_state == "on" and CONSULT_REV == 4
    r = Run(tmp_path, fake_cli, state="off")
    try:
        set_out(fake_cli[1], [("concern", "ZZC-1")], kinds=["indication"])
        row, _, _ = r.turn("가짜고민 시술")
        st = r.rows("start")[0]
        assert st["consult_state"] == "on" and st["consult_rev"] == 4 and "consult" in row
    finally:
        r.close()


def test_s8_t19_old_off_conv_opens_but_blocks_turns(tmp_path, fake_cli):
    f = tmp_path / "chat" / TURNS_FILE
    _w(f, _old_rows("off03", "off"))
    before = f.read_text(encoding="utf-8")
    assert blocked(Ledger(f.parent).get("off03"), 20) == "rev_changed"
    r = Run(tmp_path, fake_cli)
    try:
        page = r.c.get("/c/off03").text
        assert "q-old" in page and "a-old" in page
        m = re.findall(r'<[^>]*data-conv-off-closed[^>]*>(.*?)</div>', page, re.S)
        assert m and re.fullmatch(r'\s*<span data-slot="conv-off-closed"></span>\s*', m[0])  # 빈 자리 하나 · 문구 0
        assert 'data-error="rev_changed"' in r.c.post("/c/off03/turns", data={"text": "가짜고민"}).text
        assert f.read_text(encoding="utf-8") == before and r.fj.requests == [] and r.hub.requests == []
    finally:
        r.close()


def test_s8_t17_rev3_on_conv_closed(tmp_path, fake_cli):
    f = tmp_path / "chat" / TURNS_FILE
    _w(f, _old_rows("on3", "on", rev=3))
    assert blocked(Ledger(f.parent).get("on3"), 20) == "rev_changed"
    r = Run(tmp_path, fake_cli)
    try:
        assert 'data-slot="conv-outdated"' in r.c.get("/c/on3").text
        assert 'data-error="rev_changed"' in r.c.post("/c/on3/turns", data={"text": "x"}).text
    finally:
        r.close()


def test_s8_t19_off_branch_code_kept_and_one_new_slot():
    src = inspect.getsource(turn_mod.run_turn)
    assert "await walk(question, d, carry=" in src  # CP7 — 끈 가지 코드는 남는다
    tpl = "".join(p.read_text(encoding="utf-8") for p in (TEMPLATES / "partials").glob("*.html"))
    assert tpl.count('data-slot="conv-off-closed"') == 1
