"""0.3 WP-5 — 객체 구멍 · 옛 대화(SPEC-006 §8 FX1 ~ FX4 · CS7 · S6-T12 · T13 · T21 · B-5)."""

from __future__ import annotations

import json

from clinic_chat.consult import blanks
from clinic_chat.consult import plan as plan_mod
from clinic_chat.consult import state as st
from clinic_chat.consult.ledger import TURNS_FILE, Ledger, blocked
from tests.state_fixture import Run, jev
from tests.test_state_object import apply, fake_walk, t

# ───── S6-T12 guess(FX1 · FX2) ─────

def test_s6_t12_guess_again_as_guess_stays_guess():
    obj = st.empty()
    apply(obj, fake_walk([t("site:ZZS-1", "가짜부위", "uncertain_kept", 0.5)]), 1)
    diff = apply(obj, fake_walk([t("site:ZZS-1", "가짜부위", "uncertain_kept", 0.5)]), 2)
    it = st.find(obj, "site", "ZZS-1")
    assert it["state"] == "guess" and [h["turn"] for h in it["history"]] == [1, 2]  # history +1 · state 그대로
    assert not [d for d in diff if d["op"] == "state"] and st.filter_sites(obj) == []  # 거르개 아님


def test_s6_t12_target_option_bk5_yes_confirm():
    for dec, extra in (("target", {}), ("carried", {"option_from": 1})):
        obj = st.empty()
        apply(obj, fake_walk([t("site:ZZS-1", "가짜부위", "uncertain_kept", 0.5)]), 1)
        apply(obj, fake_walk([t("site:ZZS-1", "가짜부위", dec, 0.9, **extra)]), 2)
        assert st.find(obj, "site", "ZZS-1")["state"] == "confirmed", dec
    obj = st.empty()  # BK5 답 yes — 그 선택지가 걸린 선택지로 yes(how = option)
    apply(obj, fake_walk([t("concern:ZZC-1", "가짜고민", "uncertain_kept", 0.5)]), 1)
    blanks.record(obj, {"slot": "confirm", "about": "concern:ZZC-1",
                        "options": [{"table": "concern", "code": "ZZC-1", "name": "가짜고민"}]}, 1)
    apply(obj, fake_walk([t("concern:ZZC-1", "가짜고민", "carried", 0.9, option_from=1)]), 2)
    assert st.find(obj, "concern", "ZZC-1")["state"] == "confirmed"
    assert st._again("said", "guess") == "confirmed" and st._again("withdrawn", "guess") == "guess"


# ───── S6-T13 BK5 줄임(CS7) ─────

def test_s6_t13_guess_site_equal_focus_concern_site_no_bk5():
    obj = st.empty()
    apply(obj, fake_walk([t("concern:ZZC-1", "가짜고민", "target"),
                          t("site:ZZS-1", "가짜부위", "uncertain_kept", 0.5, start=5)]), 1)
    obj["concern_sites"] = [{"table": "site", "code": "ZZS-1", "name": "가짜부위", "concern_code": "ZZC-1",
                             "state": "derived"}]
    bks = blanks.count(obj, plan_mod.Plan(), False)
    assert not [b for b in bks if b["bk"] == "BK5"]
    obj["concern_sites"][0]["concern_code"] = "ZZC-9"  # 다른 고민의 부위면 그대로 BK5
    assert [b["about"] for b in blanks.count(obj, plan_mod.Plan(), False) if b["bk"] == "BK5"] == ["site:ZZS-1"]


# ───── S6-T21 옛 대화(FX3 · FX4) ─────

def _w(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _old_rows(cid, state, rev=None):
    st_row = {"conv_id": cid, "turn": 1, "phase": "start", "asked_at": "2026-11-17T09:00:00+09:00", "question": "q-old",
              "llm": {"session_id": "S"}, "consult_state": state, "prompts": {"consult_at_start": None}}
    if rev is not None:
        st_row["consult_rev"] = rev
    return [st_row, {"conv_id": cid, "turn": 1, "phase": "end", "question": "q-old", "error": None,
                     "llm": {"answered": True, "session_id": "S"}, "session_state": "open", "answer": "a-old"}]


def test_s6_t21_rev_less_on_conv_rejected_without_writing(tmp_path, fake_cli):
    f = tmp_path / "chat" / TURNS_FILE
    _w(f, _old_rows("old02", "on"))
    before = f.read_text(encoding="utf-8")
    c = Ledger(f.parent).get("old02")
    assert blocked(c, 20) == "rev_changed"
    r = Run(tmp_path, fake_cli)
    try:
        page = r.c.get("/c/old02").text
        assert 'data-slot="conv-outdated"' in page and 'href="/" data-slot="error-new-conversation"' in page
        frag = r.c.post("/c/old02/turns", data={"text": "다음 말"}).text
        assert 'data-error="rev_changed"' in frag and 'href="/"' in frag
        assert f.read_text(encoding="utf-8") == before  # B-5 — 장부 줄을 쓰지 않는다
    finally:
        r.close()


def test_s6_t21_off_conv_marked_but_runs_as_0_1(tmp_path, fake_cli):
    f = tmp_path / "chat" / TURNS_FILE
    _w(f, _old_rows("off01", "off"))
    r = Run(tmp_path, fake_cli, noul=jev(kinds={"indication": 0.9}))
    try:
        assert 'data-slot="conv-outdated"' in r.c.get("/c/off01").text  # 서버 켬 · 대화 끔
        r.cid, r.n = "off01", 1
        row, kinds, _ = r.turn("가짜고민 시술")
        assert "consult" not in row and "done" in kinds and r.rows("start")[-1]["consult_state"] == "off"
        assert "consult_rev" not in r.rows("start")[-1]
    finally:
        r.close()


def test_s6_t21_new_on_conv_has_rev_and_no_marker(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev(kinds={"indication": 0.9}))
    try:
        r.turn("가짜고민 시술")
        assert r.rows("start")[0]["consult_rev"] == 3
        assert 'data-slot="conv-outdated"' not in r.c.get(f"/c/{r.cid}").text
        row2, _, _ = r.turn("가짜시술 개요")
        assert row2["error"] is None  # rev 3 대화는 계속 받는다
    finally:
        r.close()


def test_w3_abbr_carry_keeps_original_state():
    """검수 W-3 ⓐ — 줄임으로 다시 부른 앞 대상(`by` = abbr+carry)은 객체 칸의 원래 급 그대로(history 만 +1)."""
    for first, want in (("uncertain_kept", "guess"), ("target", "said")):
        obj = st.empty()
        apply(obj, fake_walk([t("modality:ZZM-1", "가짜계열", first, 0.9)]), 1)
        diff = apply(obj, fake_walk([t("modality:ZZM-1", "가짜계열", "target", 0.55, by="abbr+carry")]), 2)
        it = st.find(obj, "modality", "ZZM-1")
        assert it["state"] == want and [h["turn"] for h in it["history"]] == [1, 2], first
        assert not [d for d in diff if d["op"] == "state"]
    obj = st.empty()  # 객체에 없으면(생기지 않는 꼴) 추정으로
    apply(obj, fake_walk([t("modality:ZZM-1", "가짜계열", "target", 0.55, by="abbr+carry")]), 1)
    assert st.find(obj, "modality", "ZZM-1")["state"] == "guess"
