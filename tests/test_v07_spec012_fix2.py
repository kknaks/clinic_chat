"""SPEC-012 고침 2 — WP-4 측정에서 떨어진 기준 · 못 닫은 구멍(고르기 칸 합집합 · HA1 조상 지움 · 계열 요약 이름만 · 값 범위 ·
가격 물음 줄 다시 · 못 찾은 대상 표지 · 1차 이해 실패 때 앞 상태 유지). 외부 호출 0."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from clinic_chat.consult import detect as dt
from clinic_chat.consult import material as mat
from tests.test_consult_material import _walk
from tests.test_v07_spec012_topics import V, ha1


def _det(sets: dict):
    d = dt.Detector.__new__(dt.Detector)
    d.kos = SimpleNamespace(sort_codes=lambda xs: sorted(xs), item={})
    d.casezero, d.errors = set(), []

    async def value_set(slot, v, res):
        return set(v["items"]) if v.get("table") == "name" else set(sets.get(v["code"], set()))

    d.value_set = value_set
    return d


def test_pick_slots_union_named_both_kept():
    """S06 T4 — 「그중에서 티타늄리프팅이랑 포트라만」: 티타늄은 이름 일치(시술 칸) · 포트라는 제품 칸 → 합집합(#13)."""
    d = _det({"S-FACE": {"T1", "T2", "P1", "P2", "X"}, "PRD-POT": {"P1", "P2"}, "PRC-NIR": {"T1", "T2", "P1", "P2"}})
    conds = {"site": [{"table": "site", "code": "S-FACE"}],
             "procedure": [{"table": "name", "code": "item:티타늄", "name": "티타늄리프팅", "items": ["T1", "T2"]},
                           {"table": "procedure", "code": "PRC-NIR", "name": "근적외선", "from": "product"}],
             "product": [{"table": "product", "code": "PRD-POT", "name": "포트라"}]}
    got = asyncio.run(d.items(conds)).codes
    assert set(got) == {"T1", "T2", "P1", "P2"}  # 서로 지우지 않음 · 제품의 시술(from product)로 넓히지 않음


def test_pick_slots_product_narrows_named_procedure():
    """「보톡스 하이톡스로」 — 직접 말한 시술이 제품 집합을 품으면 제품이 그 시술을 좁힌다(교집합 뜻 그대로)."""
    d = _det({"PRC-BTX": {"B1", "B2", "B3"}, "PRD-HTS": {"B2"}})
    conds = {"procedure": [{"table": "procedure", "code": "PRC-BTX", "name": "보톡스"}],
             "product": [{"table": "product", "code": "PRD-HTS", "name": "하이톡스"}]}
    assert asyncio.run(d.items(conds)).codes == ["B2"]


def test_ha1_drops_ancestor_when_concern_site_already_stated():
    """S10 T3 r2 · r3 — 1차가 턱 · 턱밑(고민 부위)을 다 냈어도 조상 턱을 지워 턱밑 하나(실행마다 같게)."""
    out, rec = ha1([V("S-JAW", "턱"), V("S-JAWUNDER", "턱밑"), V("C-DOUBLE", "이중턱")])
    assert [t["code"] for t in out if t["table"] == "site"] == ["S-JAWUNDER"] and rec["replaced"]


def test_value_ranges_groups_differing_values():
    rows = [{"duration_min": "3", "duration_max": "7", "duration_unit": "일"},
            {"duration_min": "0", "duration_max": "1", "duration_unit": "일"},
            {"onset_min": "1", "onset_max": "1", "onset_unit": "주"}]
    assert mat.value_ranges(rows) == {"duration": "0~7일"}
    assert mat.value_ranges([{"d_min": "1", "d_max": "2", "d_unit": "일"}, {"d_min": "1", "d_max": "2", "d_unit": "주"}]) == {}
    assert mat.value_ranges([{"d_min": "1", "d_max": "2", "d_unit": "일"}]) == {}


async def test_summary_line_names_only_when_branch_has_no_procedure(tmp_path):
    """H6 · HN1 둘째 줄 — 계열 요약 줄은 그 계열 아래 명동점 진료항목 0(시술 코드가 명동점 시술 밖)이면 이름만(marks.names_only)."""
    w = await _walk(tmp_path, "가짜고민 시술", {"indication": 0.9})
    full = mat.renumber(w, {}, {}, 1)
    thin = mat.renumber(w, {}, {}, 1, branch_procs=set())
    names = [g for g in thin.summary_groups if g["marks"].get("names_only") == "branch"]
    assert names and all(set(r) == {"n", "summary", "modality_name"} for g in names for r in g["rows"])
    assert any(x.get("names_only") for x in thin.new_lines if x.get("summary"))
    # 명동점 시술이 다 있으면 지금 그대로
    allp = {p for blk in mat.summaries(w.reads, {x for b in mat.summaries(w.reads, set(w.verified.gids()), False)
                                                    for ln in b["lines"] for x in ln["gids"]}, False)
            for ln in blk["lines"] for p in ln["proc_codes"]}
    same = mat.renumber(w, {}, {}, 1, branch_procs=allp)
    assert [g["rows"] for g in same.summary_groups] == [g["rows"] for g in full.summary_groups]


def test_kos_block_force_items_resends_list_lines():
    from tests.test_v05_llm_block import KOS, kos

    from clinic_chat.consult import kos_block as kb

    k = kos()
    code = next(iter(k.item))
    base = dict(cond={}, items=[code], cart=[], cart_rec=None, viewed=[], pairs={}, pairs_capped=False, course=[], figs=[],
                sent={"codes": {code}, "cart_sig": "", "pairs": set()}, items_max=30)
    _, m0 = kb.build(k, KOS, **base)
    _, m1 = kb.build(k, KOS, **base, force_items=True)
    assert code not in m0["codes"] and code in m1["codes"] and m1["force_items"]


def test_understand_failure_keeps_previous_topic_and_list(tmp_path, fake_cli):
    """고침 2 — 1차 이해가 실패하면(여기서는 JSON 깨짐 · 시간 초과와 같은 길) 앞 주제 · 조건 · 목록을 그대로 두고 장부에 표지."""
    import json

    from tests.state_fixture import Run, state_hub
    from tests.test_v05_detect import EXTRA_ENTRY
    from tests.test_v05_fix_a import SITE_ENTRY
    from tests.test_v05_topic_tuple import understand_raw
    from tests.v04_fixture import entry_rows, list_route

    hub = state_hub()
    hub.routes["resolve"] = list_route(entry_rows() + EXTRA_ENTRY + SITE_ENTRY)
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        (fake_cli[1] / "understand.json").write_text(understand_raw([("site", "ZS-072")]), encoding="utf-8")
        row1, _, _ = r.turn("가짜턱")
        c1 = row1["consult"]
        bad = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "깨진 { 출력"},
                         ensure_ascii=False)
        (fake_cli[1] / "understand.json").write_text(bad, encoding="utf-8")
        row2, _, _ = r.turn("가짜 말")
        c2 = row2["consult"]
        assert c2["understand"]["error"] and c2["topic"].get("understand_error")
        assert c2["active_topic"] == c1["active_topic"] and row2["items"]["codes"] == row1["items"]["codes"]
    finally:
        r.close() if hasattr(r, "close") else None
