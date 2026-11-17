"""0.3 WP-3 — 함께 쓰는 코드의 선택 인자(SPEC-006 TW2 · TW4 · TW8 · S6-T6 · WORK-014 §8 · 코디 W-4).

셋 다 안 주면 `front()` · `walk()` 가 지금과 글자 그대로 같다 — 기존 시험 다섯(test_walk_front · core · entry ·
test_consult_carry · test_state_carry)은 안 고친 채 통과한다(같은 WP 에서 돌린다). 여기는 새 인자의 꼴만.
"""

from __future__ import annotations

import inspect

from clinic_chat.columns import col_b
from clinic_chat.walk import front as front_mod
from clinic_chat.walk.entry import END_NO_ENTRY
from clinic_chat.walk.front import ABBR, front
from tests.conftest import make_settings
from tests.fake_jev import FakeJev
from tests.walk_fixture import C1, P1, cand, jev_noul, make_hub

LIST = [{"entity_table": "procedure", "entity_code": f"ZZP-{i}", "entity_name": f"x{i}", "text": f"x{i}"}
        for i in range(3)]


def abbr(c: dict, full: str) -> dict:
    x = cand(c)
    x["matched"] = {**x["matched"], "how": ABBR, "full": full}
    return x


async def run(tmp_path, hub, noul, **kw):
    s = make_settings(tmp_path)
    fj = FakeJev(noul_fn=noul)
    return await front("가짜 질문", hub.client(), fj.jev(s), s, **kw), fj


def decisions(f):
    return {t["id"]: t["decision"] for t in f.trace_front["targets"]}


def test_s6_t6_signatures_default_to_current_behavior():
    sig = inspect.signature(front)
    assert sig.parameters["entry_pick"].default is True and sig.parameters["abbr_yes_only"].default is False
    wsig = inspect.signature(col_b.walk)
    assert wsig.parameters["entry_pick"].default is True and wsig.parameters["abbr_yes_only"].default is False
    assert ABBR == "줄임" and front_mod.ABBR == ABBR


async def test_tw2_entry_pick_false_skips_fanout(tmp_path):
    f, fj = await run(tmp_path, make_hub(resolve=[], list_mode=LIST), jev_noul(kinds={"overview": 0.9}, entry=0.9),
                      entry_pick=False)
    assert f.end == END_NO_ENTRY and f.trace_entry_pick == {"skipped": True} and f.entries == []
    assert len(fj.requests) == 1  # 맨 앞 판단 한 요청뿐 — 입구 고르기 0
    f2, fj2 = await run(tmp_path, make_hub(resolve=[], list_mode=LIST), jev_noul(kinds={"overview": 0.9}, entry=0.9))
    assert f2.trace_entry_pick and f2.trace_entry_pick.get("requests", 0) >= 1  # 기본 = 지금 동작


async def test_tw2_entry_pick_false_but_carried_yes_is_cr6(tmp_path):
    carry = [{"table": "procedure", "code": "ZZP-7", "name": "가짜앞대상", "from_turn": 1}]
    f, _ = await run(tmp_path, make_hub(resolve=[]), jev_noul(kinds={"overview": 0.9}), carry=carry, entry_pick=False)
    assert [e.node for e in f.entries] == ["procedure:ZZP-7"] and f.trace_entry_pick is None


async def test_tw8_abbr_between_dropped_only_with_flag(tmp_path):
    hub = make_hub(resolve=[abbr(C1, "가짜고민전체")])
    f, _ = await run(tmp_path, hub, jev_noul(target=0.5, kinds={"indication": 0.9}), abbr_yes_only=True)
    assert decisions(f) == {"concern:ZZC-1": "dropped"} and f.end == END_NO_ENTRY
    f2, _ = await run(tmp_path, hub, jev_noul(target=0.5, kinds={"indication": 0.9}))  # 기본 = TG2 그대로
    assert decisions(f2) == {"concern:ZZC-1": "uncertain_kept"}
    f3, _ = await run(tmp_path, hub, jev_noul(target=0.1, kinds={"indication": 0.9}))  # 기본 = TG3 그대로
    assert decisions(f3) == {"concern:ZZC-1": "named"}
    f4, _ = await run(tmp_path, hub, jev_noul(target=0.9, kinds={"indication": 0.9}), abbr_yes_only=True)
    assert decisions(f4) == {"concern:ZZC-1": "target"}


async def test_tw8_partial_candidates_unchanged_with_flag(tmp_path):
    hub = make_hub(resolve=[cand(P1)])  # `matched.how` 없음(= 부분) — 0.2 판정 그대로
    f, _ = await run(tmp_path, hub, jev_noul(target=0.5), abbr_yes_only=True)
    assert decisions(f) == {"procedure:ZZP-1": "uncertain_kept"}


async def test_tw4_follow_origin_is_target_regardless_of_noul(tmp_path):
    carry = [{"table": "procedure", "code": "ZZP-7", "name": "가짜앞대상", "from_turn": 1, "origin": "follow"}]
    f, _ = await run(tmp_path, make_hub(resolve=[]), jev_noul(target=0.05, kinds={"overview": 0.9}), carry=carry,
                     entry_pick=False)
    assert [e.node for e in f.entries] == ["procedure:ZZP-7"] and f.entries[0].by == "carried"
    assert f.carried == [{"table": "procedure", "code": "ZZP-7", "from_turn": 1, "noul": 0.05, "decision": "carried",
                          "origin": "follow"}]
    plain = [{k: v for k, v in carry[0].items() if k != "origin"}]  # origin 없음 = 0.1 그대로(yes 만)
    f2, _ = await run(tmp_path, make_hub(resolve=[]), jev_noul(target=0.05, kinds={"overview": 0.9}), carry=plain)
    assert f2.carried[0]["decision"] == "dropped" and "origin" not in f2.carried[0]


async def test_w3_abbr_between_same_as_carried_target_kept(tmp_path):
    """검수 W-3 ⓐ — 앞 대상(든 후보)과 같은 (표, 코드)의 줄임은 사이 값이어도 대상(CR2 · TW8 이 서로 지우지 않게)."""
    hub = make_hub(resolve=[abbr(C1, "가짜고민전체")])
    carry = [{"table": "concern", "code": "ZZC-1", "name": "가짜고민", "from_turn": 3}]
    f, fj = await run(tmp_path, hub, jev_noul(target=0.55, kinds={"indication": 0.9}), carry=carry,
                      entry_pick=False, abbr_yes_only=True)
    assert decisions(f) == {"concern:ZZC-1": "target"} and f.end is None
    t = f.trace_front["targets"][0]
    assert t["by"] == "abbr+carry" and f.carried == []  # CR2 — 든 후보는 이번 글의 후보로 들어왔다
    assert [(e.node, e.by, e.read_as) for e in f.entries] == [
        ("concern:ZZC-1", "target", {"by": "abbr+carry", "table": "concern", "code": "ZZC-1"})]
    assert '"abbreviated": true' in str(fj.requests[0]).replace("'", '"').replace("True", "true")  # ⓑ — 줄임 신호
    other = [{"table": "concern", "code": "ZZC-9", "name": "가짜다른", "from_turn": 3}]  # 다른 앞 대상 = TW8 그대로
    f2, _ = await run(tmp_path, hub, jev_noul(target=0.55, kinds={"indication": 0.9}), carry=other,
                      entry_pick=False, abbr_yes_only=True)
    assert decisions(f2)["concern:ZZC-1"] == "dropped"
    f3, fj3 = await run(tmp_path, hub, jev_noul(target=0.55, kinds={"indication": 0.9}), carry=carry)  # 기본 = 지금 동작
    assert decisions(f3) == {"concern:ZZC-1": "uncertain_kept"} and "by" not in f3.trace_front["targets"][0]
    assert "abbreviated" not in str(fj3.requests[0])


async def test_w3b_abbr_candidate_uses_target_abbr_section(tmp_path):
    """검수 W-3 ⓑ — 줄임 후보(`abbreviated`)는 상담 프롬프트의 `target_abbr` 절로 묻는다(`target` 은 시험 앱과 같은 글자)."""
    from clinic_chat.config import ROOT
    from clinic_chat.consult.prompt import load

    jf = load(ROOT / "prompts" / "consult.json").jev_front
    assert "abbreviated" in jf["target_abbr"] and "abbreviated" not in jf["target"]
    hub = make_hub(resolve=[abbr(C1, "가짜고민전체"), cand(P1)])
    _, fj = await run(tmp_path, hub, jev_noul(target=0.9, kinds={"indication": 0.9}), instr=jf, abbr_yes_only=True)
    qs = fj.requests[0]["questions"]
    assert "abbreviated" in str(qs["target_0"]) and "abbreviated" not in str(qs["target_1"])
