"""S8-T9 · S8-T10 · S8-T11 · S8-T18(일부) — 답 재료(SPEC-008 §3 · QM1 ~ QM5 · WORK-017 WP-6). 가짜 허브 · 가짜 Jev(부르면 실패)."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from clinic_chat.columns import col_b
from clinic_chat.columns.base import Deps
from clinic_chat.consult import kinds_pick as kp
from clinic_chat.consult import material_read as mr
from clinic_chat.walk import front as front_mod
from clinic_chat.walk.calls import Entry
from clinic_chat.walk.front import FrontResult, _decide_kinds
from clinic_chat.walk.kinds import KIND_CODES
from tests.conftest import make_settings
from tests.fake_hub import row
from tests.fake_jev import FakeJev
from tests.walk_fixture import P1, cand, jev_noul, lay, make_hub


class EL:
    def name(self, t, c):
        return {"procedure:ZZP-1": "가짜시술", "procedure:ZZP-9": "가짜상대"}.get(f"{t}:{c}", "가짜")


def acc(targets, kinds, out=False):
    return SimpleNamespace(targets=[{"table": t, "code": c, "name": EL().name(t, c), "sure": True} for t, c in targets],
                           kinds=kinds, out=out)


def no_jev(name, instr, state):
    raise AssertionError("Jev 를 부르면 안 된다")


def deps(tmp_path, hub, noul=no_jev):
    s = make_settings(tmp_path)
    fj = FakeJev(noul_fn=noul)
    return Deps(s, hub.client(), fj.jev(s)), fj


# ───── S8-T9 정보 종류 ─────

@pytest.mark.parametrize("tables,kinds", [
    (["procedure"], ["mechanism"]), (["procedure"], ["overview"]), (["concern"], ["indication"]),
    (["procedure", "concern"], ["outcome", "adverse"]), (["site"], []), (["procedure"], ["same_day", "offer"]),
    (["modality"], ["lineup", "recovery"]),
])
def test_s8_t9_same_as_decide_kinds_where_ks2_ks4_do_not_touch(tmp_path, tables, kinds):
    ents = [Entry(t, f"ZZ{i}", "가짜", "target") for i, t in enumerate(tables)]
    got = kp.decide(ents, kinds, {})
    s = make_settings(tmp_path)
    ref = FrontResult()
    kv = {k: 1.0 if k in kinds else 0.0 for k in KIND_CODES}
    _decide_kinds(ref, s, kv, {k: "yes" for k in KIND_CODES if k in kinds}, ents)
    assert got.kinds == ref.kinds and got.required == ref.required and got.ranges == ref.ranges
    assert [o["code"] for o in got.offered] == [o["code"] for o in ref.offered]


def test_s8_t9_empty_kinds_default_and_overview_set():
    e = [Entry("procedure", "ZZP-1", "가짜시술", "target")]
    k0 = kp.decide(e, [], {})
    assert k0.kinds and set(k0.by.values()) == {"default"} and k0.required == []  # KS2 없음 → KS6
    k1 = kp.decide(e, ["overview"], {})
    assert {"mechanism", "indication", "outcome", "lineup"} & {k for k, v in k1.by.items() if v == "kd9"}  # KS5
    ks = kp.decide(e, ["mechanism"], {})
    assert ks.offered and all(o["by"] == "kd11" for o in ks.offered)  # KS7 — 안 고른 기본 묶음 = offered
    assert "adverse" not in ks.kinds and "adverse" in kp.decide(e, ["adverse"], {}).kinds  # KD7 — 안전 종류는 고를 때만


# ───── S8-T18 경로 나눔 · 같은 함수 ─────

def test_s8_t18_build_walk_equals_col_b_walk(tmp_path):
    """같은 가짜 허브 · 같은 대상 · 같은 종류 — 깊이 · 값 가지를 안 타는 입력에서 verified · 재료 줄 · 종료 상태가 같다."""
    hub1 = make_hub(resolve=[cand(P1)])
    d1, _ = deps(tmp_path, hub1, jev_noul(kinds={"mechanism": 0.9}))
    w1 = asyncio.run(col_b.walk("q", d1))
    hub2 = make_hub(resolve=[cand(P1)])
    d2, fj = deps(tmp_path, hub2)
    w2 = asyncio.run(mr.build_walk(d2, acc([("procedure", "ZZP-1")], ["mechanism"]), [], EL()))
    assert w1.front.kinds == w2.front.kinds and w1.front.required == w2.front.required
    assert sorted(w1.verified.gids()) == sorted(w2.verified.gids())
    assert w2.material.lines and w1.material.lines == w2.material.lines and w1.end_state == w2.end_state
    assert fj.requests == [] and not [t for t, _ in hub2.requests if t == "resolve"]  # Jev 0 · resolve 맞춤 꼴 0


def test_s8_t18_wrappers_are_one_line():
    for fn in (front_mod.coverage_of_nodes, front_mod.ranges_for, front_mod.kind_rows):
        body = [ln for ln in inspect.getsource(fn).splitlines()[1:] if ln.strip()]
        assert len(body) == 1 and "return" in body[0]


def test_qm3_no_target_and_out(tmp_path):
    d, _ = deps(tmp_path, make_hub())
    w = asyncio.run(mr.build_walk(d, acc([], []), [], EL()))
    assert w.end_state == "입구 없음"
    w = asyncio.run(mr.build_walk(d, acc([], [], out=True), [], EL()))
    assert w.end_state == "범위 밖"


# ───── S8-T10 깊이(이번 턴 대상만) ─────

def _pair_hub():
    pr = [row("g_pair", "GPR-95001", from_table="procedure", from_code="ZZP-1", from_name="가짜시술",
              to_table="procedure", to_code="ZZP-9", to_name="가짜상대", question_class="보완", a_side="from",
              a_layer=lay(), b_layer=None)]
    return make_hub(pair_rows=pr, facet_rows={"combo": pr})


def test_s8_t10_depth_only_this_turn_targets(tmp_path):
    hub = _pair_hub()
    d, fj = deps(tmp_path, hub)
    w = asyncio.run(mr.build_walk(d, acc([("procedure", "ZZP-1")], ["combo"]), [], EL()))
    dec = mr.depth_trace(w)["decisions"]
    assert dec.get("procedure:ZZP-9") == "listed_only" and mr.depth_trace(w)["read"] == []
    assert any(v.gold_table == "g_pair" for v in w.verified.rows.values())  # 폭 행은 재료에 그대로
    w2 = asyncio.run(mr.build_walk(d, acc([("procedure", "ZZP-1"), ("procedure", "ZZP-9")], ["combo"]), [], EL()))
    dec2 = mr.depth_trace(w2)["decisions"]
    assert set(dec2.values()) <= {"gate_forced", "listed_only"} and fj.requests == []
    assert all(v == "gate_forced" for k, v in dec2.items() if k in ("procedure:ZZP-1", "procedure:ZZP-9"))


# ───── S8-T11 값(값 고르기 없음) ─────

def test_s8_t11_adverse_detail_not_deferred_and_no_value_pick_mark(tmp_path):
    hub = make_hub()
    d, fj = deps(tmp_path, hub)
    w = asyncio.run(mr.build_walk(d, acc([("procedure", "ZZP-1")], ["adverse"]), [], EL()))
    facets = [c.request.get("facet") for c in w.planned]
    assert "adverse_detail" in facets or "adverse" in facets
    assert all(c in w.planned for c in [c for c in w.planned if c.request.get("facet") == "adverse_detail"])
    assert not [v for v in w.verified.rows.values() if v.marks.get("value_pick")] and fj.requests == []
