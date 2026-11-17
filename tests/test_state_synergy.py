"""WP-5 — 시너지 먼저 · 같은 날 제약 표지(S5-T11 · T21 · U5-2 ~ U5-5) — 가짜 셋 · 켠 상태 명시."""

from __future__ import annotations

import json

import pytest

from clinic_chat.consult import synergy
from tests.state_fixture import Run, jev


def _two_turns(r: Run) -> tuple[dict, dict]:
    row1, _, _ = r.turn("가짜제품 받고 싶어요")
    r.set_noul(jev({"가짜고민": 0.92, "가짜윗고민": 0.1, "가짜제품": 0.9}, kinds={"indication": 0.9}))
    row2, kinds, _ = r.turn("가짜고민 때문에요")
    return row1, row2


@pytest.fixture
def run(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev({"가짜제품": 0.9}, kinds={"overview": 0.9}))
    yield r
    r.close()


def _syn_lines(row: dict) -> list[dict]:
    out = []
    for ln in row["llm_user_message"].splitlines():
        if ln.startswith("{") and '"synergy": true' in ln:
            out.append(json.loads(ln))
    return out


def test_s5_t11_complement_lines_inside_only_once(run):
    row1, row2 = _two_turns(run)
    assert not [q for q in row1["consult"]["queries"] if q["tool"] == "pair"]  # 고민이 없으면 안 읽는다(U5-2 (a))
    pq = [q for q in row2["consult"]["queries"] if q["tool"] == "pair"]
    assert [q["request"]["classes"] for q in pq if q["rule"] == "QY4"] == [["보완"]]
    lines = _syn_lines(row2)
    assert [x["name"] for x in lines] == ["가짜계열", "가짜시술둘"]  # 리스트 안 상대만 · 허브 순서(밖 상대 ZZM-8 은 줄 없음)
    assert lines[0]["same_day"] == [] and lines[1]["same_day"] == ["가능"]  # 빈칸은 같은 날 값 없음(SY3)
    assert "rows" not in lines[0]  # 0.3 EM4 — LLM 에 행 수를 주지 않는다(달리 받음 ④ · 검수 W-2)
    assert [q["total"] for q in pq if q["rule"] == "QY4"] == [4]  # 행 수는 장부 `queries` 로 센다
    by_n = {x["n"]: x for x in row2["material"]["new_lines"] if x.get("summary")}
    assert len(by_n[lines[0]["n"]]["gids"]) == 2  # 상대마다 한 줄(행 둘 — 장부 gid)
    j = json.loads(row2["llm_user_message"].split("\n")[1])
    assert j["synergy_lines"] == [x["n"] for x in lines]
    bundle = row2["list"]["synergy"][0]
    assert [p["code"] for p in bundle["inside"]] == ["ZZM-1", "ZZP-2"] and bundle["outside_n"] == 1
    run.set_noul(jev({"가짜고민": 0.9, "가짜제품": 0.9}, kinds={"overview": 0.9}))
    row3, _, _ = run.turn("가짜시술 은 뭐예요")
    again = [q for q in row3["consult"]["queries"] if q["tool"] == "pair" and q["request"]["a"] == "product:ZZR-1"]
    assert again == []  # 같은 대상은 한 번(SY9)
    assert {q["request"]["a"] for q in row3["consult"]["queries"] if q["rule"] == "QY4"} == {"procedure:ZZP-1"}  # SY10
    assert all(x["with"] != "가짜제품" for x in _syn_lines(row3))


def test_focus_change_rebuilds_bundle_without_hub(run):
    _two_turns(run)
    run.set_noul(jev({"가짜윗고민": 0.95, "가짜제품": 0.9, "가짜고민": 0.1}, kinds={"indication": 0.9}))
    n0 = len(run.hub.requests)
    row3, _, _ = run.turn("가짜윗고민 이요")
    pairs = [x for x in run.hub.requests[n0:] if x[0] == "pair"]
    assert pairs == [] and row3["consult"]["after"]["focus"] == "ZZC-2"
    rec = row3["consult"]["after"]["shown"]["synergy"][0]
    assert [b["concern_code"] for b in rec["by_focus"]] == ["ZZC-1", "ZZC-2"]
    assert _syn_lines(row3) == []  # LLM 줄은 다시 안 싣는다(SY9)


def test_s5_t21_caution_mark_no_material(run):
    _, row2 = _two_turns(run)
    lines = {x["name"]: x for x in _syn_lines(row2)}
    assert lines["가짜계열"]["same_day_caution"] is True and lines["가짜시술둘"]["same_day_caution"] is False
    msg = row2["llm_user_message"]
    for gid in ("GPR-90101", "GPR-90102"):  # QY4b 의 행 — 메시지 · 실은 gid · shown.info 에 0
        assert gid not in msg
        assert gid not in json.dumps([x for x in row2["material"]["new_lines"] if not x.get("summary")])
        assert gid not in json.dumps(row2["consult"]["after"]["shown"]["info"])
        assert gid not in json.dumps(row2["list"])
    rec = row2["consult"]["after"]["shown"]["synergy"][0]
    assert rec["caution_gids"] == ["GPR-90101"]  # 장부에는 표지 근거 gid 만
    assert [q["request"]["classes"] for q in row2["consult"]["queries"] if q["rule"] == "QY4b"] == [["회피", "간격", "순서"]]
    mod = row2["list"]["concerns"][0]["modalities"][0]
    assert mod["modality_code"] == "ZZM-1" and mod["caution"] is True and mod["synergy"] is True


def test_synergy_off_reads_nothing(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev({"가짜제품": 0.9}, kinds={"overview": 0.9}), chat_synergy="off")
    _, row2 = _two_turns(r)
    assert not [q for q in row2["consult"]["queries"] if q["tool"] == "pair"] and not row2["list"]["synergy"]
    r.close()


def test_partner_side_and_inside():
    row = {"from_table": "modality", "from_code": "M1", "from_name": "a", "to_table": "product", "to_code": "P1",
           "to_name": "b", "a_side": "to"}
    assert synergy.partner(row, "product:P1") == ("modality", "M1", "a")
    p = {"table": "product", "code": "P9", "reach": {"procedures": ["X"], "modalities": []}}
    assert synergy.inside(p, {"X"}, set()) and not synergy.inside(p, set(), {"X"})
