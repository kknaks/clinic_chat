"""0.6 WP-4 · WP-5 — 이름 목록(T24) · 답 확인 기록(T25) · 상품 이름 입구(T31) · 값 고르기(T32) · 같은 날 근거(T33) · 모의(가짜 이름)."""

from __future__ import annotations

import re

from clinic_chat.columns import col_b
from clinic_chat.walk.keycodes import key_codes
from clinic_chat.walk.material import answer_check, roster_text
from tests.fake_hub import row
from tests.test_walk_core import deps
from tests.walk_fixture import P1, PR, cand, jev_noul, lay, make_hub, product_rows

CODE = re.compile(r"\b[A-Z]{1,4}-\d{1,5}\b")
S1 = {"entity_table": "site", "entity_code": "ZZS-1", "entity_name": "가짜", "text": "가짜", "norm": "가짜"}
MD = {"entity_table": "modality", "entity_code": "ZZD-1", "entity_name": "가짜계열", "text": "가짜계열"}
P2 = {"entity_table": "procedure", "entity_code": "ZZP-2", "entity_name": "다른시술", "text": "다른시술"}


def _rows():
    pr = product_rows()
    for i, r in enumerate(pr["outcome"]):
        r["effect_code"] = f"ZZE-{i}"
    return pr


def _hub():
    return make_hub(resolve=[cand(PR)], facet_rows=_rows(), coverage={"product:ZZR-1": {"outcome": (0, 4, 1)}})


async def _outcome_walk(tmp_path, **kw):
    return await col_b.walk("q", deps(tmp_path, _hub(), jev_noul(kinds={"outcome": 0.95}), **kw))


async def test_t24_roster_same_codes_split_no_dup_first_page(tmp_path):
    w = await _outcome_walk(tmp_path)
    r = w.material.roster
    got = {x["code"] for k in ("entry", "value", "layer") for x in r[k]}
    want = {c for v in w.verified.rows.values() for c in key_codes(v.row)}
    assert got == want
    assert {x["code"] for x in r["layer"]} == {"product:ZZR-2", "procedure:ZZP-1"}  # 그 행을 가진 하위 · 윗층 대상
    assert [x["name"] for x in r["value"]] == ["가짜효과0", "가짜효과1", "가짜효과2", "가짜효과3", "윗층효과"]
    text = roster_text(r)
    assert text and not CODE.search(text)
    names = [n for grp in __import__("json").loads(text).values() for ns in grp.values() for n in ns]
    assert len(names) == len(set(names))
    # 쪽 나눔이 돌아도 이름 목록은 첫 쪽에만 · 전부
    from clinic_chat.walk.fold import FoldTooLarge

    ps: list[str] = []
    for mx in range(200, 4000, 20):
        try:
            ps = col_b.prompt_pages("q", w, mx, "sys")
        except FoldTooLarge:
            continue
        break
    assert len(ps) > 1 and "## 이름 목록" in ps[0] and all("## 이름 목록" not in p for p in ps[1:])
    assert w.gate["ok"], w.gate


def test_t25_answer_check_records_missing_value_and_layer_only():
    r = {"entry": [{"code": "procedure:ZZP-9", "name": "물은시술"}],
         "value": [{"code": "effect:ZZE-1", "name": "가짜효과 하나"}, {"code": "effect:ZZE-2", "name": "가짜효과둘"},
                   {"code": "effect:ZZE-3", "name": "가짜효과셋"}],
         "layer": [{"code": "product:ZZR-2", "name": "가짜하위제품"}]}
    ans = "가짜효과하나 와 가짜하위제품"  # 값 둘 · 층 0 이 빠짐(공백은 보지 않는다)
    c = answer_check(ans, r)
    assert c["missing"]["value"] == ["가짜효과둘", "가짜효과셋"] and c["missing"]["layer"] == []
    assert c["said"] == {"value": 1, "layer": 1} and c["missing_codes"]["value"] == ["effect:ZZE-2", "effect:ZZE-3"]
    c2 = answer_check("가짜효과하나 가짜효과둘 가짜효과셋", r)
    assert c2["missing"] == {"value": [], "layer": ["가짜하위제품"]}


async def test_t25_answer_check_is_not_a_gate(tmp_path, fake_cli):
    bin_, route, log = fake_cli
    from tests.conftest import stream
    (route / "answer.jsonl").write_text(stream("가짜효과0 만 말함"), encoding="utf-8")
    w_ans = await col_b.run("q", deps(tmp_path, _hub(), jev_noul(kinds={"outcome": 0.95}), claude_cli_bin=bin_),
                            b_on=False)
    res = w_ans.result
    ac = res["trace"]["answer_check"]
    assert ac and "가짜효과1" in ac["missing"]["value"] and res["answer"] == "가짜효과0 만 말함"
    assert res["end_state"] == "근거 충족"


OFFER = [row("g_offer", f"GOF-9600{i}", offer_id=f"ZZO-{i}", offer_name="가짜시술 상품", item_table="procedure",
             item_code="ZZP-1", price_low=1, price_high=2) for i in range(2)]


async def test_t31_offer_name_is_entry_and_covers_inner_piece(tmp_path):
    """T31 — 상품 이름 길 → `entry.picked[].by` = `offer_name` · 그 이름 안의 조각(yes 아님)은 덮인 조각(dropped)."""
    hub = make_hub(resolve=[cand(MD, start=0)], facet_rows={"offer": OFFER})
    w = await col_b.walk("가짜계열 가격", deps(tmp_path, hub, jev_noul(target=0.5, kinds={"offer": 0.9})))
    f = w.front
    picked = f.trace_entry["picked"]
    assert [p["id"] for p in picked if p["by"] == "offer_name"] == ["offer:ZZO-0", "offer:ZZO-1"]
    t = {x["id"]: x for x in f.trace_front["targets"]}["modality:ZZD-1"]
    assert t["decision"] == "dropped" and t["covered_by"] == "가짜계열"
    assert not [e for e in f.entries if e.node == "modality:ZZD-1"]
    # yes 면 대상 그대로 · 상품 이름 길이 0 행이면 덮지 않는다(B-8)
    w2 = await col_b.walk("가짜계열 가격", deps(tmp_path, make_hub(resolve=[cand(MD, start=0)], facet_rows={"offer": OFFER}),
                                            jev_noul(target=0.9, kinds={"offer": 0.9})))
    assert any(e.node == "modality:ZZD-1" for e in w2.front.entries)
    w3 = await col_b.walk("가짜계열 가격", deps(tmp_path, make_hub(resolve=[cand(MD, start=0)], facet_rows={"offer": []}),
                                            jev_noul(target=0.5, kinds={"offer": 0.9})))
    assert any(e.node == "modality:ZZD-1" for e in w3.front.entries)


def _vp_hub(detail_log, *, with_adverse_entry=False):
    adv = [row("g_adverse", f"GAD-9700{i}", target_table="procedure", target_code="ZZP-1", adverse_code=f"ZZA-{i}",
               adverse_name=f"가짜사건{i}", polarity="보고", silver_table="adverse_risk", silver_id=f"ZR-{i}",
               layer=lay()) for i in range(1, 3)]
    res = [cand(P1, start=0), cand({**S1}, start=4)]
    if with_adverse_entry:
        res.append(cand({"entity_table": "adverse", "entity_code": "ZZA-1", "entity_name": "가짜사건1",
                         "text": "가짜사건1"}, start=7))
    hub = make_hub(resolve=res, list_mode=[{"entity_table": "adverse", "entity_code": "ZZA-1",
                                             "entity_name": "가짜사건1", "text": "가짜부위사건", "norm": "가짜부위사건"}])

    def facets_fn(p):
        flt = p.get("filters") or {}
        if p.get("facet") == "adverse":
            if flt.get("site_name"):
                return {"rows": adv[:1], "ledger_check": []}  # 부위로 거른 조회 — 1 행
            if flt.get("adverse_code"):
                return {"rows": [a for a in adv if a["adverse_code"] == flt["adverse_code"]], "ledger_check": []}
            return {"rows": adv, "ledger_check": []}
        if p.get("facet") == "adverse_detail":
            detail_log.append(dict(flt))
            rs = [row("g_adverse_detail", f"GAX-97{i}", target_table="procedure", target_code="ZZP-1",
                      adverse_risk_id=f"ZR-{i % 2 + 1}", detail_kind="징후", layer=lay()) for i in range(100, 104)]
            return {"rows": [r for r in rs if not flt.get("adverse_risk_id") or r["adverse_risk_id"] == flt[
                "adverse_risk_id"]], "ledger_check": []}
        return {"rows": [], "ledger_check": []}
    hub.routes["facets"] = facets_fn
    return hub


def _vp_noul(value_yes: int = 0):
    return jev_noul(target=lambda i, st: 0.9 if i == 0 else 0.9, kinds={"adverse": 0.9},
                    other=lambda name: 0.9 if name == f"value_{value_yes}" else 0.1)


async def test_t32_value_pick_marks_uncovers_filter_and_narrows_detail(tmp_path):
    """T32 — 값 입구 없음 + required 값 종류 → 값 고르기 · yes 값에 표지 · 그 값 이름이 품는 부위 조각의 거르개가 걷혀 다시 읽힘 ·
    미룬 상세는 그 사건 위험 id 로만(W-7)."""
    log: list = []
    w = await col_b.walk("q", deps(tmp_path, _vp_hub(log), _vp_noul(0)))
    vp = w.front.trace_entry["value_pick"]
    assert vp["requests"] == 1 and vp["picked"] == ["adverse:ZZA-1"] and vp["uncovered_filters"] == ["site:ZZS-1"]
    assert {"id": "adverse:ZZA-1", "by": "value_pick", "via": None} in w.front.trace_entry["picked"]
    marked = [v.gid for v in w.verified.rows.values() if v.marks.get("value_pick")]
    assert marked == ["GAD-97001"]
    # 부위 거르개를 걷은 adverse 조회(필터 없음)가 다시 읽혀 둘째 사건 행도 verified
    assert "GAD-97002" in w.verified.gids()
    assert log == [{"adverse_risk_id": "ZR-1"}] and vp["br5"]["risk_ids"] == 1
    assert w.gate["ok"], w.gate
    # VP 가 yes 를 못 내면 미룬 상세를 전부(필터 없이 — 부위 거르개가 있으면 그대로)
    log2: list = []
    w2 = await col_b.walk("q", deps(tmp_path, _vp_hub(log2), _vp_noul(99)))
    assert w2.front.trace_entry["value_pick"]["picked"] == [] and {str(x) for x in log2} == {str({"site_name": "가짜"})}


async def test_t32_detail_ids_per_target_own_rows(tmp_path):
    """T32(W-1) — 대상 둘 · 같은 yes 사건의 위험 id 가 대상마다 다름 → 미룬 상세는 대상마다 자기 응답 행의 id 만(곱 아님)."""
    adv = {"procedure:ZZP-1": [("ZZA-1", "ZR-1"), ("ZZA-2", "ZR-3")], "procedure:ZZP-2": [("ZZA-1", "ZR-2")]}
    log: list = []
    hub = make_hub(resolve=[cand(P1, start=0), cand(P2, start=6)])

    def facets_fn(p):
        node, flt = p.get("node"), p.get("filters") or {}
        t, c = node.split(":", 1)
        if p.get("facet") == "adverse":
            return {"rows": [row("g_adverse", f"GAD-9{t[:1]}{c[-1]}{i}", target_table=t, target_code=c, adverse_code=a,
                                 adverse_name=f"가짜사건{a[-1]}", polarity="보고", silver_table="adverse_risk",
                                 silver_id=rid, layer=lay()) for i, (a, rid) in enumerate(adv.get(node, []))],
                    "ledger_check": []}
        if p.get("facet") == "adverse_detail":
            log.append((node, flt.get("adverse_risk_id")))
            return {"rows": [], "ledger_check": []}
        return {"rows": [], "ledger_check": []}
    hub.routes["facets"] = facets_fn
    noul = jev_noul(target=0.9, kinds={"adverse": 0.9}, other=lambda name: 0.9 if name == "value_0" else 0.1)
    w = await col_b.walk("q", deps(tmp_path, hub, noul))
    vp = w.front.trace_entry["value_pick"]
    assert vp["picked"] == ["adverse:ZZA-1"]
    assert sorted(log) == [("procedure:ZZP-1", "ZR-1"), ("procedure:ZZP-2", "ZR-2")]
    assert vp["br5"]["calls"] == 2 and vp["br5"]["per_call_ids"] == [1, 1]


async def test_t32_no_request_when_value_entry_or_not_required(tmp_path):
    log: list = []
    w = await col_b.walk("q", deps(tmp_path, _vp_hub(log, with_adverse_entry=True), _vp_noul(0)))
    assert "value_pick" not in w.front.trace_entry
    noul = jev_noul(kinds={"adverse": 0.55, "overview": 0.9}, other=lambda name: 0.9)  # adverse = kd10(required 아님)
    w2 = await col_b.walk("q", deps(tmp_path, _vp_hub([]), noul))
    assert "value_pick" not in w2.front.trace_entry


async def test_t33_same_day_package_basis_with_layer_mark(tmp_path):
    """T33 — `same_day` 빈칸 + 같은 날 조합 패키지(항목 = 윗층) → 「같은 날 근거(패키지)」 + 층 표지."""
    hub = make_hub(resolve=[cand(PR, start=0), cand(P2, start=6)])
    pkg = {"package_id": "ZZG-1", "package_name": "가짜패키지", "package_kind": "같은 날 조합", "rows": [
        row("g_package", "GPK-96001", package_id="ZZG-1", package_name="가짜패키지", package_kind="같은 날 조합",
            item_table="procedure", item_code="ZZP-1", item_name="가짜시술"),
        row("g_package", "GPK-96002", package_id="ZZG-1", package_name="가짜패키지", package_kind="같은 날 조합",
            item_table="procedure", item_code="ZZP-2", item_name="다른시술")]}
    hub.routes["pair"] = {"rows": [row("g_pair", "GPR-96001", from_table="product", from_code="ZZR-1",
                                       to_table="procedure", to_code="ZZP-2", a_side="from", same_day="",
                                       question_class="보완", a_layer=lay())],
                          "same_day_packages": [pkg], "ledger_check": []}

    def scope_fn(p):
        if p.get("node") == "product:ZZR-1":
            return {"holders": [row("g_scope", "GSC-96001", role="물려받음", holder_table="procedure",
                                    holder_code="ZZP-1", subject_table="product", subject_code="ZZR-1")]}
        return {"holders": []}
    hub.routes["scope"] = scope_fn
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"same_day": 0.9})))
    m = {v.gid: v.marks for v in w.verified.rows.values()}
    assert m["GPK-96001"].get("same_day_package") and m["GPK-96002"].get("same_day_package")
    assert m["GPK-96001"]["same_day_layer"] == [{"of": "가짜제품", "layer": "물려받음"}]
    assert "same_day_layer" not in m["GPK-96002"] and m["GPR-96001"]["same_day_blank"]
    assert w.gate["ok"], w.gate


def test_wp7_report_fills_v06_lines_from_trace():
    """WP-7 — 새 trace 칸으로 「B 전용」 의 0.6 줄이 채워진다(합성 줄)."""
    from clinic_chat.report import render

    trace = {"front": {"thresholds": {"kind_read": 0.5}, "targets": [
                {"id": "procedure:ZZP-1", "decision": "target"}, {"id": "site:ZZS-1", "decision": "named"},
                {"id": "product:ZZR-1", "decision": "target"}],
             "kinds": [{"code": "overview", "decision": "read", "by": "top"},
                       {"code": "outcome", "decision": "read", "by": "kd9"}],
             "offered": [{"code": "package", "by": "kd11"}], "groups": [{"members": ["procedure:ZZP-1",
                                                                                   "product:ZZR-1"], "by": "scope"}],
             "coverage": {"procedure:ZZP-1": {"outcome": {"self": 3, "lower": 0, "inherited": 0}}}},
             "entry": {"picked": [{"id": "offer:ZZO-1", "by": "offer_name"}],
                       "value_pick": {"requests": 1, "picked": ["adverse:ZZA-1"],
                                      "br5": {"before_rows_coverage_adverse": 10, "after_detail_rows": 4}}},
             "breadth": [{"tool": "facets", "mark": "narrowed"}, {"tool": "pair", "widened": "w4"}],
             "visited": [{"purpose": "group", "node": "procedure:ZZP-1"}, {"purpose": "group", "node": "product:ZZR-1"}],
             "answer_check": {"missing": {"value": ["가짜효과"], "layer": []}}}
    rows = [{"run_id": "r1", "set_id": "S1", "build": {}, "models": {}, "settings": {},
             "columns": {"b": {"timing": {}, "end_state": "근거 충족", "calls": {}, "tokens": {}, "trace": trace}}}]
    md = render(rows, [], title="t", items={"S1": {"range": "시술", "expected_kinds": ["overview"]}})
    want = ("기대 종류 덮음 · `by` 별 | 1/1 · top 1", "기대 밖으로 읽은 종류(kd9 · kd10) | 1 · coverage 어림 행 3 · 문항 S1",
            "KD11 로 `offered` 로 남긴 종류 | 1 · 그 가운데 기대 종류 0", "BR6 으로 넓힌 조회(named · uncertain_kept) | 1 · 0",
            "W-4 S1", "무리 하나 1", "요청 1 · yes 값 1 · BR5 를 좁힌 문항 S1", "줄어든 행(coverage adverse 셈 − 읽은 상세 행) 6",
            "합 1 · 중앙 1", "상품 이름 입구가 선 문항 | 1")
    for x in want:
        assert x in md, x
