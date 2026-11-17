"""0.6 WP-6 — 키 갈래(DU1 · DU2 · `codes_by_class` · T35) · 갈래 함수(`walk.keycodes`) · 대조의 「같음」(검수 W-3)."""

from __future__ import annotations

import json

from clinic_chat.evalset import answer_key as ak
from clinic_chat.quality import checks
from clinic_chat.quality.checks import NA
from clinic_chat.quality.grade import synthetic_inputs
from clinic_chat.walk.calls import Call
from clinic_chat.walk.keycodes import by_class, classify, value_side
from tests.fake_hub import row

SYN = synthetic_inputs()


def test_classify_entry_value_layer_and_priority():
    lay_row = row("g_outcome", "GOU-90001", target_table="product", target_code="ZZR-2", effect_code="ZZE-1")
    pair_row = row("g_pair", "GPR-90001", from_table="procedure", from_code="ZZP-1", to_table="product",
                   to_code="ZZR-2")
    cls = classify([(lay_row, False)], {"procedure:ZZP-1"})
    assert cls == {"product:ZZR-2": "layer", "effect:ZZE-1": "value"}
    # 같은 코드가 값 자리(짝의 끝)에도 나오면 값 > 층 · 대상 자신은 입구
    cls = classify([(lay_row, False), (pair_row, False)], {"procedure:ZZP-1"})
    assert cls == {"product:ZZR-2": "value", "effect:ZZE-1": "value", "procedure:ZZP-1": "entry"}
    # 값 쪽 조회(고민 시작)의 후보 = 값
    ind = row("g_indication", "GIN-90001", candidate_table="procedure", candidate_code="ZZP-3", concern_code="ZZC-1")
    assert classify([(ind, True)], {"concern:ZZC-1"}) == {"procedure:ZZP-3": "value", "concern:ZZC-1": "entry"}
    assert value_side({"concern_code": "ZZC-1"}) and value_side({"node": "adverse:ZZA-1"})
    assert not value_side({"node": "procedure:ZZP-1"}) and value_side({"node": "procedure:ZZP-1"}, {"from_group": "x"})


async def test_t35_codes_by_class_sum_and_d3_na():
    """T35 — `codes_by_class` 합 = `codes[]`(겹침 0) · 값 코드 0 문항의 D3 = 해당 없음 · 층은 따로 · 세 열 같은 분모."""
    rows = [row("g_adverse_detail", "GAX-90001", target_table="product", target_code="ZZR-2"),
            row("g_adverse", "GAD-90001", target_table="procedure", target_code="ZZP-1", adverse_code="ZZA-1")]
    per = [{"call": Call("facets", {"node": "procedure:ZZP-1", "facet": "adverse"}), "rows": rows}]
    spec = {"entries": [{"table": "procedure", "code": "ZZP-1"}, {"table": "adverse", "code": "ZZA-1"},
                        {"table": "modality", "code": "ZZD-1", "lower_of": "procedure:ZZP-1"}]}
    key_rows = [{"gid": r["gid"]} for r in rows]
    cls = ak.key_classes(spec, key_rows, per)
    codes = ["product:ZZR-2", "procedure:ZZP-1", "adverse:ZZA-1"]
    cbc = by_class(codes, cls)
    assert cbc == {"entry": ["procedure:ZZP-1", "adverse:ZZA-1"], "value": [], "layer": ["product:ZZR-2"]}
    assert sorted(sum(cbc.values(), [])) == sorted(codes)
    key = {**SYN["key"], "codes": codes, "codes_by_class": cbc}
    outs = {c: checks.run(column=c, answer="가짜", cited=[], trace={}, key=key, key_rows=SYN["key_rows"],
                          gold=SYN["gold"]) for c in ("a", "b", "c")}
    for c, o in outs.items():
        d3 = o["D3"]
        assert d3["na"] == NA and d3["missing_ratio"] is None and d3["key_codes"] == 0, c
        assert d3["layer_codes"] == 1 and d3["layer_missing"] == ["product:ZZR-2"], c
    # 옛 키(갈래 없음)는 지금 식 그대로
    old = checks.run(column="b", answer="가짜", cited=[], trace={}, key={**SYN["key"], "codes": codes},
                     key_rows=SYN["key_rows"], gold=SYN["gold"])
    assert old["D3"]["key_codes"] == 3 and "by_class" not in old["D3"]


def test_w3_diff_same_includes_codes_and_calls_sha(tmp_path):
    a, b = tmp_path / "prev", tmp_path / "now"
    a.mkdir()
    b.mkdir()
    base = {"id": "Q90", "rows": [{"gold_table": "g_adverse", "gid": "GAD-90001", "bucket": "rows"}], "codes": ["x:1"],
            "unnamed_codes": [], "side_codes": [], "kinds_met": [], "expected_end_state": "근거 충족",
            "expected_end_state_by": "status", "sample_check": {"pointers": 0, "in_key": 0, "missing": []},
            "calls_sha256": "s1", "build": {"gold_hash": "g", "aux_hash": "x"}, "ledger_check": []}
    (a / "Q90.json").write_text(json.dumps(base), encoding="utf-8")
    (b / "Q90.json").write_text(json.dumps({**base, "codes_by_class": {"entry": [], "value": ["x:1"], "layer": []},
                                            "script_sha256": "new"}), encoding="utf-8")
    r = ak.diff_keys(a, b)[0]
    assert r["same"] and r["calls_same"] and r["codes_by_class"] == {"entry": 0, "value": 1, "layer": 0}
    (b / "Q90.json").write_text(json.dumps({**base, "codes": ["x:1", "x:2"], "calls_sha256": "s2"}), encoding="utf-8")
    r = ak.diff_keys(a, b)[0]
    assert not r["same"] and r["same_rows"] and r["differs"] == ["codes"] and not r["calls_same"]
