"""WP-4 — 빈 칸 · `ask` · 프롬프트 절(S5-T9 · T10 · T19 · 코디 W-2 · W-4 · W-7) — 가짜 셋 · 켠 상태 명시."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from clinic_chat.consult import blanks
from clinic_chat.consult import plan as plan_mod
from clinic_chat.consult import state as st
from clinic_chat.consult.prompt import STATE_SECTION_IDS, load, parse
from tests.state_fixture import INDICATION, Run, jev
from tests.test_state_object import apply, fake_walk, t

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "consult.json"
SYSTEM_0_1_SHA = "b3fa8e2080077c3dc42cc053b5a84779c22d6c730b42267e11dec0f2be577440"  # 0.1(+ 수정 1) 보관본의 끈 시스템 글


def opt(code):
    return {"table": "concern", "code": code, "name": code, "layer": "x"}


def pick(bl, obj, turn, **kw):
    base = {"new_nodes": set(), "new_guess": set(), "prev_ask": None, "end_state": "근거 충족", "kinds_read": [],
            "ask_max": 2}
    base.update(kw)
    return blanks.pick(bl, obj, turn, **base)


def obj_with(*targets, turn=1):
    o = st.empty()
    apply(o, fake_walk(list(targets)), turn)
    return o


def test_s5_t9_deterministic_new_item_first_and_limits():
    o = obj_with(t("product:ZZR-1", "가짜제품", "target"), t("site:ZZS-1", "가짜부위", "uncertain_kept", 0.5, start=5))
    p = plan_mod.Plan(options={"product:ZZR-1": [opt("C1"), opt("C2")]})
    bl = blanks.count(o, p, False)
    assert {b["bk"] for b in bl} == {"BK2", "BK5"}
    a1 = pick(bl, o, 1, new_nodes={"product:ZZR-1", "site:ZZS-1"}, new_guess={"site:ZZS-1"})
    a2 = pick(copy.deepcopy(bl), copy.deepcopy(o), 1, new_nodes={"product:ZZR-1", "site:ZZS-1"}, new_guess={"site:ZZS-1"})
    assert a1 == a2 and a1[0]["slot"] == "concern" and a1[0]["why"] == "NQ2-1"  # 같은 장부 → 같은 ask · 새 항목 먼저
    for turn in (1, 2):
        blanks.record(o, a1[0], turn)
    bl3 = blanks.count(o, p, False)
    a3, why = pick(bl3, o, 3)
    assert a3["bk"] == "BK5"  # 같은 빈 칸 세 번째는 안 준다(NQ4 — 두 번까지)


def test_nq5_nq6_nq7_nq9():
    o = obj_with(t("product:ZZR-1", "가짜제품", "target"))
    p = plan_mod.Plan(options={"product:ZZR-1": [opt("C1")]})
    bl = blanks.count(o, p, False)
    assert pick(bl, o, 2, prev_ask={"answered": False}, new_nodes={"product:ZZR-1"})[1] == "NQ5"  # 새 항목 무관(W-2)
    assert pick(bl, o, 2, kinds_read=["adverse"])[1] == "NQ6"
    assert pick(bl, o, 2, end_state="범위 밖")[1] == "NQ7"
    empty = blanks.count(o, plan_mod.Plan(options={"product:ZZR-1": []}), False)
    assert pick(empty, o, 2)[0] is None  # NQ9 — 선택지 0 은 안 준다


def test_w4_bk3_bk4_beat_bk1_and_fewer_wins():
    o = obj_with(t("concern:ZZC-2", "가짜윗고민", "target"))
    sub2 = [opt("C3"), opt("C4")]
    sites3 = [{"table": "site", "code": f"S{i}", "name": "s", "layer": ""} for i in range(3)]
    sites2 = sites3[:2]
    assert [b["bk"] for b in blanks.count(o, plan_mod.Plan(), True)] == ["BK1"]
    assert [b["bk"] for b in blanks.count(o, plan_mod.Plan(sub_concerns=sub2), True)] == ["BK3"]
    assert [b["bk"] for b in blanks.count(o, plan_mod.Plan(sub_concerns=sub2, sub_sites=sites3), True)] == ["BK3"]
    assert [b["bk"] for b in blanks.count(o, plan_mod.Plan(sub_concerns=sub2 + [opt("C5")], sub_sites=sites2),
                                          True)] == ["BK4"]
    assert [b["bk"] for b in blanks.count(o, plan_mod.Plan(sub_concerns=sub2, sub_sites=sites2), True)] == ["BK3"]  # B-8


def test_w1_site_only_blank_and_not_with_focus():
    o = obj_with(t("site:ZZS-1", "가짜부위", "target"))
    p = plan_mod.Plan(options={"site:ZZS-1": [opt("C1")]})
    a, _ = pick(blanks.count(o, p, False), o, 1, new_nodes={"site:ZZS-1"})
    assert a["slot"] == "concern" and a["about"] == "site:ZZS-1" and a["bk"] == "BK2s"
    apply(o, fake_walk([t("concern:ZZC-1", "가짜고민", "target")]), 2)
    assert not [b for b in blanks.count(o, p, True) if b["bk"] == "BK2s"]


def test_s5_t10_options_are_hub_rows_and_llm_gets_names(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev({"가짜제품": 0.9}, kinds={"overview": 0.9}))
    row, _, _ = r.turn("가짜제품 받고 싶어요")
    codes = {x["concern_code"] for x in INDICATION["product:ZZR-1"]}
    assert row["consult"]["ask"]["options"] and {o["code"] for o in row["consult"]["ask"]["options"]} <= codes
    j = json.loads(row["llm_user_message"].split("\n")[1])
    assert j["ask"]["slot"] == "concern" and all(set(o) <= {"name", "layer", "n"} for o in j["ask"]["options"])
    assert "ZZC-" not in row["llm_user_message"]  # NQ8 — 코드는 장부에만
    ns = [o["n"] for o in j["ask"]["options"]]
    assert ns and all(any(x["n"] == n and x.get("rule") == "QY3" for x in row["material"]["new_lines"]) for n in ns)
    r.close()


def test_s5_t19_system_texts_and_loader(tmp_path, fake_cli):
    p = load(PROMPT)
    assert hashlib.sha256(p.system_text.encode()).hexdigest() == SYSTEM_0_1_SHA  # 끈 글 = 0.1 글자 그대로(W-7)
    ids = [x["id"] for x in json.loads(PROMPT.read_text(encoding="utf-8"))["system"]]
    assert ids == ["role", "rules", "screen", "material", "tail"]
    on = p.system_text_state
    heads = [x["lines"][0] for x in json.loads(PROMPT.read_text(encoding="utf-8"))["system_state"]]
    i_screen, i_mat = on.index("## 화면"), on.index("## 자료를 얻는 법")
    assert all(i_screen < on.index(h) < i_mat for h in heads) and p.state_ready
    assert tuple(x["id"] for x in json.loads(PROMPT.read_text(encoding="utf-8"))["system_state"]) == STATE_SECTION_IDS
    d = json.loads(PROMPT.read_text(encoding="utf-8"))
    d.pop("system_state")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    assert not parse(bad.read_bytes()).state_ready
    r = Run(tmp_path / "on", fake_cli, chat_prompt_file=str(bad))
    frag = r.c.post(f"/c/{r.cid}/turns", data={"text": "가짜고민"}).text
    assert 'data-error="prompt"' in frag or "prompt" in frag  # 켠 대화 시작 실패(오류 종류 prompt)
    assert not (tmp_path / "on" / "chat" / "turns.jsonl").exists()
    r.close()
    r2 = Run(tmp_path / "off", fake_cli, state="off", chat_prompt_file=str(bad))
    row, kinds, _ = r2.turn("가짜고민 시술")
    assert "done" in kinds and row["error"] is None  # 끈 대화는 된다
    r2.close()
