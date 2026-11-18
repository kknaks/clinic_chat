"""답 키 고치기 1 — K1 ~ K4 세트 확정값 명세 · 키 전용 윗층 / 하위 기준 대상 · K5 시간 섞기(G13 · G14 · D11) · K6 하위 목록.

모의 허브 · 가짜 코드(ZZ…) · 가짜 이름(「가짜…」)만 쓴다.
"""

from __future__ import annotations

from clinic_chat.evalset import answer_key as ak
from clinic_chat.evalset.key_calls import build_spec
from clinic_chat.evalset.key_table import kinds_cell, target_cell
from clinic_chat.evalset.loader import spec_entries
from clinic_chat.quality import checks
from clinic_chat.quality.checks import NA
from clinic_chat.quality.gold_codes import GoldCodes, row_spans, text_spans
from clinic_chat.walk import gate
from clinic_chat.walk.calls import Entry, key_calls, plan_calls
from clinic_chat.walk.material import build, unfold
from clinic_chat.walk.verified import Verified
from tests.fake_hub import FakeHub, row

NAMES = {"procedure:ZZP-1": "가짜시술", "modality:ZZM-1": "가짜계열", "concern:ZZC-1": "가짜고민",
         "concern:ZZC-0": "가짜윗고민", "procedure:ZZP-2": "가짜하위시술", "adverse:ZZA-1": "가짜사건"}


# ───── K6 — lineup 은 scope 로 읽고 RQ1 셈에 든다(키 rows[] 에는 g_scope 행이 없다) ─────

def test_k6_key_calls_keep_scope_only_for_lineup():
    p = Entry("procedure", "ZZP-1", "가짜시술")
    with_lineup = key_calls(plan_calls([p], ["overview", "lineup"]))
    assert [c.tool for c in with_lineup] == ["facets", "scope"]
    assert all(c.tool != "scope" for c in key_calls(plan_calls([p], ["overview", "outcome"])))


async def test_k6_lineup_met_by_lower_and_rows_have_no_g_scope():
    hub = FakeHub({
        "facets": {"rows": [row("g_profile", "GPF-90001", subject_table="procedure", subject_code="ZZP-1",
                                overview="가짜 개요", layer={"role": "자기"})], "ledger_check": []},
        "scope": {"holders": [row("g_scope", "GSC-90001", subject_table="procedure", subject_code="ZZP-1",
                                  holder_table="procedure", holder_code="ZZP-1", role="자기"),
                              row("g_scope", "GSC-90002", subject_table="procedure", subject_code="ZZP-1",
                                  holder_table="product", holder_code="ZZD-1", role="하위 제품")]}})
    calls = [{**c.spec(), "kinds": list(c.kinds)} for c in
             key_calls(plan_calls([Entry("procedure", "ZZP-1")], ["overview", "lineup"]))]
    spec = {"entries": [{"table": "procedure", "code": "ZZP-1"}], "expected_kinds": ["overview", "lineup"],
            "calls": calls}
    st: dict = {}
    rows, recs, lc, build_, raw = await ak.read_rows(hub.client(), calls, "procedure", st)
    met = ak.kinds_met(spec, st["per_call"])
    assert {m["kind"]: m["met_by"] for m in met} == {"overview": "self", "lineup": "lower"}
    assert all(r["gold_table"] != "g_scope" for r in rows)
    assert [x["gid"] for x in st["lineup"].values()] == ["GSC-90002"]
    k = ak.build_key("Q90", spec, rows, recs, lc, build_, set(), {"status": "답함"}, {}, raw, "sha", met=met,
                     lineup=st["lineup"])
    assert k["lineup_rows"][0]["codes"] == ["procedure:ZZP-1"] and "holder" in k["lineup_rows"][0]
    assert k["expected_end_state"] == "근거 충족"


# ───── K1 ~ K4 — 세트 확정값이 gold-build 초안을 이긴다 · 키 전용 기준 대상 ─────

async def test_set_values_override_gold_build_draft():
    hub = FakeHub({"resolve": {"candidates": []}})
    q = {"id": "Q90", "range": "부작용", "entry": "가짜시술", "need_time": True}
    item = {"expected_entries": ["modality:ZZM-1", "adverse:ZZA-1"], "expected_kinds": ["recovery", "adverse"],
            "key_lower_entries": [{"node": "procedure:ZZP-2", "of": "modality:ZZM-1"}], "key_fix1": {"at": "x"}}
    spec = await build_spec(q, hub.client(), {"gold_tables": "g_adverse"}, item, NAMES)
    assert [(e["table"], e["code"]) for e in spec["entries"]] == [("modality", "ZZM-1"), ("adverse", "ZZA-1"),
                                                                  ("procedure", "ZZP-2")]
    assert spec["entries"][2]["lower_of"] == "modality:ZZM-1" and spec["key_fix1"]
    assert spec["expected_kinds"] == ["adverse", "recovery"] and spec["kinds_from"] == "set"
    assert spec["asked_fields"]  # 구조 값(need_time)은 gold-build 에서 그대로
    assert {c["request"]["node"] for c in spec["calls"]} == {"modality:ZZM-1", "procedure:ZZP-2"}
    assert spec_entries(spec) == ["modality:ZZM-1", "adverse:ZZA-1"]  # 키 전용 대상은 B 의 기대 대상이 아니다
    assert not [r for r in hub.requests if r[0] == "resolve"]  # 세트 값이면 이름 맞춤을 하지 않는다


def test_fold_upper_counts_as_inherited_and_lower_as_lower():
    items = [{"target": "concern:ZZC-1", "kind": "indication", "self": 0, "lower": 0, "inherited": 0, "met_by": None},
             {"target": "concern:ZZC-0", "kind": "indication", "self": 5, "lower": 2, "inherited": 0, "met_by": "self",
              "upper_of": "concern:ZZC-1"},
             {"target": "procedure:ZZP-2", "kind": "adverse", "self": 3, "lower": 1, "inherited": 1, "met_by": "self",
              "lower_of": "modality:ZZM-1"}]
    out = ak.fold_extra(items)
    by = {(i["target"], i["kind"]): i for i in out}
    assert set(by) == {("concern:ZZC-1", "indication"), ("modality:ZZM-1", "adverse")}
    c = by[("concern:ZZC-1", "indication")]
    assert (c["met_by"], c["inherited"], c["inherited_via"]) == ("inherited", 7, ["concern:ZZC-0"])
    m = by[("modality:ZZM-1", "adverse")]
    assert (m["met_by"], m["lower"], m["lower_via"]) == ("lower", 5, ["procedure:ZZP-2"])


async def test_q10_form_upper_concern_key_is_inherited_and_state_from_set():
    def cc(p):
        if p.get("concern_code") == "ZZC-0":
            return {"groups": [{"candidate_table": "procedure", "candidate_code": "ZZP-1", "rows": [
                row("g_indication", "GIN-90001", candidate_table="procedure", candidate_code="ZZP-1",
                    concern_code="ZZC-0", procedure_code="ZZP-1")]}], "ledger_check": []}
        return {"groups": [], "ledger_check": [{"code": "ZZC-1", "lck_ids": ["LCK-9"], "on": "request"}]}

    hub = FakeHub({"concern_candidates": cc, "facets": {"rows": [], "ledger_check": []}})
    q = {"id": "Q90", "range": "부위·고민", "entry": "가짜고민"}
    item = {"expected_entries": ["concern:ZZC-1"], "expected_end_state": "근거 충족",
            "key_upper_entries": [{"node": "concern:ZZC-0", "of": "concern:ZZC-1"}]}
    spec = await build_spec(q, hub.client(), {"gold_tables": "g_indication", "status": "원장 확인"}, item, NAMES)
    st: dict = {}
    h = hub.client()
    rows, recs, lc, build_, raw = await ak.read_rows(h, spec["calls"], spec["range_code"], st)
    met = ak.kinds_met(spec, st["per_call"])
    assert [(m["target"], m["met_by"]) for m in met] == [("concern:ZZC-1", "inherited")]
    k = ak.build_key("Q90", spec, rows, recs, lc, build_, set(), {"status": "원장 확인"}, item, raw, "sha",
                     judgement=await ak.judge_rows(h, spec), met=met)
    assert (k["expected_end_state"], k["expected_end_state_by"]) == ("근거 충족", "set")
    assert "GIN-90001" in {r["gid"] for r in k["rows"]}
    assert "윗층만 — 가짜윗고민 기준" in kinds_cell(spec, k) and "윗층 기준: 가짜윗고민" in target_cell(spec)
    assert any("윗층 기준 대상" in f for f in ak.summary_row(k, spec, {"status": "원장 확인"})["flags"])


# ───── K5 — 시간 섞기: 표지(G13 · G14 · K4) · 답 뒤 D11 ─────

def test_time_spans():
    assert text_spans("3~6개월 유지 · 7-14일 내 소실 · 24시간") == {("3", "6", "개월"), ("7", "14", "일"), ("24", "24", "시간")}
    assert row_spans({"onset_text": "", "duration_min": "2", "duration_max": "3", "duration_unit": "주"},
                     "g_adverse") == {("2", "3", "주")}
    assert row_spans({"text": "", "min": "1", "max": "", "unit": "일"}, "g_recovery") == {("1", "1", "일")}


def _vrows():
    v = Verified()
    v.add_response({"rows": [
        row("g_outcome", "GOU-90001", target_table="procedure", target_code="ZZP-1", target_name="가짜시술",
            duration_text="3~6개월", layer={"role": "자기"}),
        row("g_adverse", "GAD-90001", target_table="procedure", target_code="ZZP-1", target_name="가짜시술",
            duration_text="7-14일", layer={"role": "자기"}),
        row("g_adverse", "GAD-90002", target_table="product", target_code="ZZD-1", target_name="가짜하위제품",
            duration_text="2주", layer={"role": "하위 제품", "holder": {"holder_name": "가짜하위제품"}}),
        row("g_adverse", "GAD-90003", target_table="procedure", target_code="ZZP-1", target_name="가짜시술",
            duration_text="", layer={"role": "자기"})]}, ranges=("procedure",), kinds=("outcome", "adverse"),
        depth="breadth", call_no=0)
    return v


def test_g13_g14_marks_and_gate_k4_requires_them():
    v = _vrows()
    m = build(v)
    by_n = {u["n"]: u["marks"] for u in unfold(m.groups)}
    marks = {x["gid"]: by_n[x["n"]] for x in m.lines}
    assert "부작용 시간 아님" in marks["GOU-90001"]["time_of"]
    assert marks["GAD-90001"]["time_of"] == "부작용 시간" and "about" not in marks["GAD-90001"]
    assert marks["GAD-90002"]["about"] == "가짜하위제품"
    assert "time_of" not in marks["GAD-90003"]  # 시간 칸이 비면 표지 없음
    ok = gate.check(reads=[], verified=v, entries=[], decisions={}, ranges=["procedure"], kinds=["outcome", "adverse"],
                    material=m, planned=[], required={})
    assert ok["K"]["K4"]["bad_n"] == 0 and {g["id"] for g in ok["gates"]} >= {"G13", "G14"}
    for g in m.groups:  # 표지를 지우면 K4 가 걸린다
        g["marks"].pop("time_of", None)
        g["marks"].pop("about", None)
        for r in g["rows"]:
            (r.get("marks") or {}).pop("time_of", None)
            (r.get("marks") or {}).pop("about", None)
    bad = gate.check(reads=[], verified=v, entries=[], decisions={}, ranges=["procedure"], kinds=["outcome", "adverse"],
                     material=m, planned=[], required={})
    lacks = {x for b in bad["K"]["K4"]["bad"] for x in b["lack"]}
    assert not bad["K"]["K4"]["ok"] and {"time_of", "about"} <= lacks


GOLD = GoldCodes(times=[("g_outcome", "procedure:ZZP-1", frozenset({("3", "6", "개월")})),
                        ("g_adverse", "procedure:ZZP-1", frozenset({("7", "14", "일")})),
                        ("g_adverse", "procedure:ZZP-9", frozenset({("1", "2", "주")}))])
KEY = {"expected_kinds": ["adverse"], "codes": [], "rows": [
    {"gid": "GAD-90001", "gold_table": "g_adverse", "bucket": "rows", "layer_role": "자기", "codes": [], "side_codes": []}]}
KR = {"GAD-90001": row("g_adverse", "GAD-90001", target_table="procedure", target_code="ZZP-1", duration_text="7-14일")}


def d11(answer, key=KEY):
    return checks.run(column="a", answer=answer, cited=[], trace={}, key=key, key_rows=KR, gold=GOLD)["D11"]


def test_d11_effect_period_as_adverse_time_and_other_target_time():
    assert d11("부기는 7~14일 안에 빠진다")["violations"] == 0
    g13 = d11("부작용은 3~6개월 지속된다")
    assert g13["G13"] == ["3~6개월"] and g13["violations"] == 1
    g14 = d11("부작용은 1~2주 간다")
    assert g14["G14"] == ["1~2주"] and not g14["G13"]
    assert d11("부작용은 3~6개월", {**KEY, "expected_kinds": ["adverse", "outcome"]})["G13"] == []  # 효과도 물었으면 안 본다
    assert d11("효과는 3~6개월", {**KEY, "expected_kinds": ["outcome"]}) == NA  # 부작용 · 회복을 묻지 않은 문항
    for col in ("b", "c"):  # 세 열 같은 함수
        assert checks.run(column=col, answer="부작용은 3~6개월", cited=[], trace={}, key=KEY, key_rows=KR,
                          gold=GOLD)["D11"]["G13"] == ["3~6개월"]
