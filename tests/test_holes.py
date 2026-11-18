"""WORK-008 WP-2 — 허브 0.5.2 응답 받기(모의): HG1 윗고민 물려받음 · HG2 자손 층 · HG3 부위 묶임(WORK-007 §8 「응답 꼴」).

모의 허브 · 가짜 코드(ZZ…) · 가짜 이름(「가짜…」)만 쓴다. 응답은 손으로 적은 꼴뿐(규칙 계산 없음).
"""

from __future__ import annotations

from clinic_chat.columns import col_b
from clinic_chat.evalset import answer_key as ak
from clinic_chat.walk import end_state as es
from clinic_chat.walk import gate
from clinic_chat.walk.calls import Call
from clinic_chat.walk.material import SITE_MATCH_KO, build, to_lines, unfold
from clinic_chat.walk.required import count_rows, met_by, rows_for_count
from clinic_chat.walk.verified import Verified
from tests.fake_hub import row
from tests.test_walk_core import deps, req
from tests.walk_fixture import C1, cand, jev_noul, make_hub

M1 = {"entity_table": "modality", "entity_code": "ZZM-1", "entity_name": "가짜계열", "text": "가짜계열"}
LCK = [{"code": "ZZC-1", "lck_ids": ["LCK-900"], "on": "request"}]


def _upper_rows(n: int = 2) -> list[dict]:
    return [row("g_indication", f"GIN-9800{i}", candidate_table="procedure", candidate_code=f"ZZP-{i + 1}",
                procedure_code=f"ZZP-{i + 1}", procedure_name=f"가짜시술{i}", asked_concern_code="ZZC-0",
                asked_concern_name="가짜윗고민", concern_code="ZZC-2", concern_name="가짜형제고민", concern_match="하위 고민",
                reach="시술 직접", hier={"asked_code": "ZZC-1", "match": "상위"}) for i in range(n)]


def cc_hg1(p):
    """HG1 — 물은 고민(ZZC-1)에 자기 행 0 → 허브가 가장 가까운 윗고민(ZZC-0)의 행을 물려줌(머리 `inherited_from`)."""
    rows = _upper_rows()
    return {"inherited_from": {"concern_code": "ZZC-0", "steps": 1},
            "counts": {"rows": len(rows), "self_rows": 0, "by_site_match": None},
            "groups": [{"candidate_table": "procedure", "candidate_code": r["candidate_code"], "rows": [r]} for r in rows],
            "modality_only": [], "unreached": [], "ledger_check": LCK}


def cc_self(p):
    """자기 행이 있는 고민(`inherited_from: null` — 0.5.1 과 같은 셈)."""
    r = row("g_indication", "GIN-98100", candidate_table="procedure", candidate_code="ZZP-1", reach="시술 직접",
            hier={"asked_code": "ZZC-1", "match": "직접"})
    return {"inherited_from": None, "counts": {"rows": 1, "self_rows": 1},
            "groups": [{"candidate_table": "procedure", "candidate_code": "ZZP-1", "rows": [r]}],
            "modality_only": [], "unreached": [], "ledger_check": []}


# ───── HG1 — 행 0 고민 → 윗고민 행 · RQ1 inherited · 근거 충족((가) 아님) ─────

async def test_hg1_upper_rows_meet_as_inherited_and_not_ga(tmp_path):
    hub = make_hub(resolve=[cand(C1)], coverage={"concern:ZZC-1": {"indication": (0, 0, 2)}})
    hub.routes["concern_candidates"] = cc_hg1
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"indication": 0.95})))
    r = req(w, "indication")
    assert (r["self"], r["inherited"], r["met_by"]) == (0, 2, "inherited") and r["inherited_via"] == ["concern:ZZC-0"]
    assert r["coverage_mismatch"] is None  # coverage {self 0, inherited 2} = 읽은 셈(좁힘 없는 조회)
    assert w.end_state == es.OK and w.reason is None, (w.end_state, w.reason, w.gate)
    assert any(x["lck_ids"] == ["LCK-900"] for x in es.reads_lc(w.reads))  # ledger_check 는 기록에 남는다
    fr = next(i for i in w.front.trace_front["required"] if i["kind"] == "indication")
    assert fr["inherited_via"] == ["concern:ZZC-0"] and fr["met_by"] == "inherited"
    lines = {x["gid"]: x for x in w.material.lines}
    marks = {lines[g]["gid"]: m for g in lines for m in [_marks(w.material)[lines[g]["n"]]]}
    for g in ("GIN-98000", "GIN-98001"):
        assert marks[g]["hier"] == "상위" and marks[g]["inherited_fill"] is True
        assert lines[g]["inherited_from"] == {"concern_code": "ZZC-0", "steps": 1}  # 기록에만
    # 표지를 떼면 K4 실패
    m = w.material
    for g in m.groups:
        g["marks"].pop("inherited_fill", None)
        for x in g["rows"]:
            (x.get("marks") or {}).pop("inherited_fill", None)
    bad = gate.check(reads=w.reads, verified=w.verified, entries=w.front.entries, decisions=w.depth.decisions,
                     ranges=w.front.ranges, kinds=w.front.kinds, material=m, planned=w.planned,
                     required=w.depth.required)
    assert "K4" in bad["fails"] and any("inherited_fill" in b["lack"] for b in bad["K"]["K4"]["bad"])


def _marks(m) -> dict[int, dict]:
    return {u["n"]: u["marks"] for u in unfold(m.groups)}


async def test_hg1_self_concern_counts_self_and_package_upper_stays_out(tmp_path):
    hub = make_hub(resolve=[cand(C1)], pkg_upper=2)
    hub.routes["concern_candidates"] = cc_self
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"indication": 0.9, "package": 0.9})))
    assert req(w, "indication")["met_by"] == "self" and "inherited_via" not in req(w, "indication")
    pk = req(w, "package")  # facets package 값 쪽 「상위」 는 inherited 로 세지 않는다(지금과 같다)
    assert pk["inherited"] == 0 and pk["self"] == 2


def test_hg1_counting_is_by_head_not_hier_match():
    up = rows_for_count(cc_hg1({}))
    assert count_rows(up, "indication")["inherited"] == 2
    pkg = [row("g_package", "GPK-90001", hier={"match": "상위"})]
    assert count_rows(rows_for_count({"rows": pkg}), "package") == {"self": 1, "lower": 0, "inherited": 0}
    assert es.counted_rows(up) and not es.counted_rows(pkg)  # (가) 셈: 물려받은 행은 센다 · package 상위는 뺀다


async def test_hg1_key_and_b_same_met_by():
    hub = make_hub(resolve=[cand(C1)])
    hub.routes["concern_candidates"] = cc_hg1
    calls = [{**Call("concern_candidates", {"concern_code": "ZZC-1"}, ("indication",)).spec(), "kinds": ["indication"]}]
    spec = {"entries": [{"table": "concern", "code": "ZZC-1"}], "expected_kinds": ["indication"], "calls": calls}
    st: dict = {}
    rows, recs, lc, build_, raw = await ak.read_rows(hub.client(), calls, "concern", st)
    met = ak.kinds_met(spec, st["per_call"])
    assert [(m["met_by"], m["inherited_via"]) for m in met] == [("inherited", ["concern:ZZC-0"])]
    assert met_by(count_rows(rows_for_count(cc_hg1({})), "indication")) == met[0]["met_by"]
    assert {r.get("inherited_from") for r in rows if r["gold_table"] == "g_indication"} == {"ZZC-0"}
    k = ak.build_key("Q90", spec, rows, recs, lc, build_, set(), {"status": "답함"}, {}, raw, "sha", met=met)
    assert k["expected_end_state"] == "근거 충족"  # 0.5.1 은 (가)


# ───── HG2 — 계열 → 시술 → 케이스(depth 2 · via) ─────

def _hg2_rows() -> list[dict]:
    p1 = {"holder_table": "procedure", "holder_code": "ZZP-1", "holder_name": "가짜시술"}
    return [
        row("g_indication", "GIN-96001", candidate_table="procedure", candidate_code="ZZP-1", reach="시술 직접",
            layer={"role": "하위 시술", "path": "", "holder": p1, "depth": 1}),
        row("g_indication", "GIN-96002", candidate_table="procedure_case", candidate_code="ZZK-1",
            procedure_name="가짜시술", reach="케이스 직접",
            layer={"role": "하위 케이스", "path": "", "depth": 2, "scope_gid": "GSC-96002",
                   "holder": {"holder_table": "procedure_case", "holder_code": "ZZK-1", "holder_name": "가짜케이스1"},
                   "via": [{"holder_table": "procedure", "holder_code": "ZZP-1", "scope_gid": "GSC-96001"}]}),
        row("g_indication", "GIN-96003", candidate_table="procedure_case", candidate_code="ZZK-2",
            procedure_name="가짜시술2", reach="케이스 직접",
            layer={"role": "하위 케이스", "path": "", "depth": 2, "scope_gid": "GSC-96004",
                   "holder": {"holder_table": "procedure_case", "holder_code": "ZZK-2", "holder_name": "가짜케이스2"},
                   "via": [{"holder_table": "procedure", "holder_code": "ZZP-9", "scope_gid": "GSC-96003"}]}),
    ]


async def test_hg2_descendant_rows_marked_and_counted_lower(tmp_path):
    hub = make_hub(resolve=[cand(M1)], facet_rows={"indication": _hg2_rows()})
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"indication": 0.95})))
    r = req(w, "indication")
    assert (r["self"], r["lower"], r["met_by"]) == (0, 3, "lower") and w.gate["ok"], w.gate
    mk = _marks(w.material)
    n = {x["gid"]: x["n"] for x in w.material.lines}
    assert mk[n["GIN-96002"]]["layer_depth"] == 2 and mk[n["GIN-96002"]]["layer_via"] == ["가짜시술"]  # 같은 응답 · 호출 0
    assert mk[n["GIN-96002"]]["speak_name"] == "가짜시술" and mk[n["GIN-96003"]]["speak_name"] == "가짜시술2"
    assert mk[n["GIN-96003"]]["layer_depth"] == 2 and "layer_via" not in mk[n["GIN-96003"]]  # 못 얻으면 지어내지 않음
    assert "layer_depth" not in mk[n["GIN-96001"]]
    assert {mk[n[g]]["holder"] for g in ("GIN-96002", "GIN-96003")} == {"가짜케이스1", "가짜케이스2"}
    rec = {x["gid"]: x for x in w.material.lines}
    assert rec["GIN-96003"]["layer_via_unnamed"] == ["procedure:ZZP-9"]  # 코드는 기록에만
    assert "ZZP-9" not in to_lines(w.material.groups)
    labels = [x for x in w.reads if x.call.purpose == "label" and x.call.meta.get("label_for") == "layer_via"]
    assert [x.call.node() for x in labels] == ["procedure:ZZP-9"]  # depth 1 행에 이름이 없을 때만 label 조회
    assert all(not isinstance(m.get("depth"), int) for m in mk.values())  # 깊이 결정 표지와 이름이 안 겹친다


async def test_hg2_label_fallback_gives_name_and_k4_needs_layer_depth(tmp_path):
    hub = make_hub(resolve=[cand(M1)], facet_rows={"indication": _hg2_rows()[1:]})
    base = hub.routes["facets"]

    def facets(p):
        if p.get("node") == "procedure:ZZP-9" and p.get("facet") == "profile":
            return {"rows": [row("g_profile", "GPF-96009", subject_table="procedure", subject_code="ZZP-9",
                                 name_ko="가짜아홉시술", layer={"role": "자기"})], "ledger_check": []}
        return base(p)
    hub.routes["facets"] = facets
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"indication": 0.95})))
    n = {x["gid"]: x["n"] for x in w.material.lines}
    assert _marks(w.material)[n["GIN-96003"]]["layer_via"] == ["가짜아홉시술"]
    assert "GPF-96009" not in w.verified.gids()  # label 조회 행은 verified 밖(K5)
    m = w.material
    for g in m.groups:
        g["marks"].pop("layer_depth", None)
        for x in g["rows"]:
            (x.get("marks") or {}).pop("layer_depth", None)
    bad = gate.check(reads=w.reads, verified=w.verified, entries=w.front.entries, decisions=w.depth.decisions,
                     ranges=w.front.ranges, kinds=w.front.kinds, material=m, planned=w.planned,
                     required=w.depth.required)
    assert any("layer_depth" in b["lack"] for b in bad["K"]["K4"]["bad"])


def test_hg2_steps_two_and_old_rows_do_not_break():
    v = Verified()
    v.add_response({"inherited_from": {"concern_code": "ZZC-0", "steps": 2}, "groups": [{"rows": _upper_rows(1)}]},
                   ranges=("concern",), kinds=("indication",), depth="breadth", call_no=0)
    v.add_response({"rows": [row("g_indication", "GIN-96100", candidate_table="procedure", candidate_code="ZZP-1",
                                 layer={"role": "하위 시술"})]},  # 0.5.1 꼴(depth · via 없음)
                   ranges=("procedure",), kinds=("indication",), depth="breadth", call_no=1)
    m = build(v, inherited_kinds={"indication"})
    mk = _marks(m)
    assert mk[1]["inherited_fill"] and "layer_depth" not in mk[2]


# ───── HG3 — 고민 + 부위 → site_match[] 표지 ─────

def _hg3_resp(with_match: bool = True) -> dict:
    a = row("g_indication", "GIN-97002", candidate_table="procedure", candidate_code="ZZP-1", reach="시술 직접",
            site_name="가짜부위", layer={"role": "자기"})
    b = row("g_indication", "GIN-97003", candidate_table="procedure", candidate_code="ZZP-2", reach="시술 직접",
            layer={"role": "자기"})
    if with_match:
        a["site_match"] = [{"by": "column"}, {"by": "concern_site", "csi_ids": ["CSI-9001"]}]
        b["site_match"] = [{"by": "concern_site", "csi_ids": ["CSI-9001"]}]
    return {"inherited_from": None, "counts": {"rows": 2, "unfiltered_rows": 9, "self_rows": 9,
                                               "by_site_match": {"column": 1, "concern_site": 2}},
            "groups": [{"rows": [a, b]}]}


def test_hg3_site_match_marks_both_values_and_k4():
    v = Verified()
    v.add_response(_hg3_resp(), ranges=("concern",), kinds=("indication",), depth="breadth", call_no=0)
    m = build(v)
    mk = _marks(m)
    assert mk[1]["site_match"] == [SITE_MATCH_KO["column"], SITE_MATCH_KO["concern_site"]]
    assert mk[2]["site_match"] == [SITE_MATCH_KO["concern_site"]]
    assert "CSI-9001" not in to_lines(m.groups) and m.lines[0]["site_match"][1]["csi_ids"] == ["CSI-9001"]
    ok = gate.check(reads=[], verified=v, entries=[], decisions={}, ranges=["concern"], kinds=["indication"],
                    material=m, planned=[], required={})
    assert ok["K"]["K4"]["ok"], ok["K"]["K4"]
    for g in m.groups:
        g["marks"].pop("site_match", None)
        for x in g["rows"]:
            (x.get("marks") or {}).pop("site_match", None)
    bad = gate.check(reads=[], verified=v, entries=[], decisions={}, ranges=["concern"], kinds=["indication"],
                     material=m, planned=[], required={})
    assert any("site_match" in b["lack"] for b in bad["K"]["K4"]["bad"])


def test_hg3_absent_field_is_051_shape():
    v = Verified()
    v.add_response(_hg3_resp(False), ranges=("concern",), kinds=("indication",), depth="breadth", call_no=0)
    m = build(v)
    assert all("site_match" not in u["marks"] for u in unfold(m.groups))
    assert gate.check(reads=[], verified=v, entries=[], decisions={}, ranges=["concern"], kinds=["indication"],
                      material=m, planned=[], required={})["K"]["K4"]["ok"]


def test_offer_inherited_zero_and_no_lower_offer_mark():
    """WP-2 표 11 — 상품 행에 「하위에 따로 있는 상품」 표지를 만들지 않는다(행의 칸으로 가른다)."""
    v = Verified()
    v.add_response({"rows": [row("g_offer", "GOF-90001", offer_id="ZZO-1", offer_name="가짜상품", item_name="가짜항목",
                                 layer={"role": "하위 제품", "holder": {"holder_name": "가짜하위제품"}})]},
                   ranges=("offer",), kinds=("offer",), depth="breadth", call_no=0)
    mk = _marks(build(v))[1]
    assert set(mk) <= {"layer", "holder", "path", "basis"}, mk


# ───── WP-4 — 구멍 묶음 draft 꼴(`pick --holes`) ─────

def test_hole_drafts_shape_question_empty_and_iii_two_options():
    from clinic_chat.evalset.pick import ancestors, descendants_or_self, hole_drafts

    parent = {"ZZC-0": None, "ZZC-1": "ZZC-0", "ZZC-2": "ZZC-1"}
    assert ancestors("ZZC-2", parent) == ["ZZC-1", "ZZC-0"] and descendants_or_self("ZZC-1", parent) == {"ZZC-1", "ZZC-2"}
    forms = {"HG1_i": [{"node": "concern:ZZC-1", "rows": 3}], "HG2_i": [{"node": "modality:ZZM-1"}],
             "HG2_ii": [{"a": "modality:ZZM-1", "b": "modality:ZZM-2", "kind": "same_day", "rows": 2}],
             "HG2_iii_a": [{"node": "modality:ZZM-1"}], "HG2_iii_b": [{"node": "modality:ZZM-2", "products": ["product:ZZR-1"]}],
             "HG3_iv": [{"concern": "concern:ZZC-1", "site": "site:ZZS-1", "rows": 0, "expected_end_state": "후보 소진"}]}
    d = hole_drafts(forms)
    assert [x["id"] for x in d] == [f"H{i:02d}" for i in range(1, 11)]
    assert all(x["question"] == "" and x["status"] == "draft" and x["source"] == "구멍" for x in d)
    assert d[0]["expected_entries"] == ["concern:ZZC-1"] and d[1]["expected_entries"] == []  # 후보 없으면 비움
    assert d[4]["range"] == "동시 가능" and len(d[5]["options"]) == 2 and d[5]["expected_entries"] == []
    assert d[9]["expected_end_state"] == "후보 소진"
