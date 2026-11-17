"""WP-4 — 답 재료(싣는 칸 · 줄 번호 · 표지) · gate K1 ~ K5 · G12 · RQ1 셈(모의)."""

from __future__ import annotations

import re

from clinic_chat.columns import col_b
from clinic_chat.columns.base import Deps
from clinic_chat.walk import end_state as es
from clinic_chat.walk.kinds import KIND_CODES, LLM_COLUMNS, llm_columns
from clinic_chat.walk.material import build, to_lines, unfold
from clinic_chat.walk.required import count_rows, met_by
from clinic_chat.walk.verified import Verified
from tests.conftest import make_settings
from tests.fake_hub import row
from tests.fake_jev import FakeJev
from tests.walk_fixture import P1, PR, cand, jev_noul, lay, make_hub, product_rows

BAD = re.compile(r"\bG[A-Z]{2}-\d{5}\b|silver_|_code\"|price_mid_raw|evidence_kind")


def deps(tmp_path, hub, noul, **kw):
    s = make_settings(tmp_path, **kw)
    return Deps(s, hub.client(), FakeJev(noul_fn=noul).jev(s))


def vfrom(rows, kinds, table_ranges=("procedure",)):
    v = Verified()
    v.add_response({"rows": rows}, ranges=table_ranges, kinds=kinds, depth="breadth", call_no=0)
    return v


async def test_material_has_no_ids_and_lines_are_complete(tmp_path):
    """완료 1 · 2 — gid · silver_ · _code 칸 · price_mid_raw · evidence_kind 0 · 줄 번호 1.. 빠짐없이 · 행 수 = 줄 수."""
    hub = make_hub(resolve=[cand(PR)], facet_rows=product_rows())
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={k: 0.9 for k in ("overview", "mechanism", "outcome")})))
    assert w.end_state == es.OK, w.gate
    text = to_lines(w.material.groups)
    assert not BAD.search(text)
    assert [x["n"] for x in w.material.lines] == list(range(1, len(w.verified.rows) + 1))
    assert len(unfold(w.material.groups)) == len(w.verified.rows) == len(w.material.lines)
    assert set(w.material.lines[0]) == {"n", "gid", "gold_table", "silver_table", "silver_id", "evidence_kind", "kind",
                                        "range", "layer", "depth"}


def test_columns_per_table_kind_equal_spec_table():
    """완료 3 — 표 · 종류마다 재료의 칸 집합 = §3-2a 표(13 줄 전부)."""
    pairs = {("g_profile", "overview"): ("overview",), ("g_profile", "mechanism"): ("mechanism",),
             ("g_indication", "indication"): ("indication",), ("g_outcome", "outcome"): ("outcome",),
             ("g_scope", "lineup"): ("lineup",), ("g_adverse", "adverse"): ("adverse",),
             ("g_adverse_detail", "adverse"): ("adverse",), ("g_recovery", "recovery"): ("recovery",),
             ("g_contraindication", "contraindication"): ("contraindication",), ("g_pair", "pair"): ("avoid",),
             ("g_package", "package"): ("package",), ("g_offer", "offer"): ("offer",)}
    for (t, k), kinds in pairs.items():
        assert llm_columns(t, kinds) == LLM_COLUMNS[(t, k)], (t, k)
    assert llm_columns("g_profile", ("indication",), followup=True) == LLM_COLUMNS[("g_profile", "indication")]
    both = llm_columns("g_profile", ("overview", "mechanism"))
    assert set(both) == set(LLM_COLUMNS[("g_profile", "overview")]) | set(LLM_COLUMNS[("g_profile", "mechanism")])
    for k in ("combo", "same_day", "order_interval"):
        assert llm_columns("g_pair", (k,)) == LLM_COLUMNS[("g_pair", "pair")]
    v = vfrom([row("g_profile", "GPF-90001", subject_table="procedure", subject_code="Z", name_ko="x", extra_col="숨김",
                   layer=lay())], ["overview"])
    flat = unfold(build(v).groups)[0]
    assert "extra_col" not in flat and set(flat) - {"n", "marks"} == set(LLM_COLUMNS[("g_profile", "overview")])


def test_listed_only_same_columns_as_deepen():
    rows = [row("g_profile", f"GPF-9000{i}", subject_table="procedure", subject_code=f"Z{i}", name_ko="x", layer=lay())
            for i in range(2)]
    v = vfrom(rows, ["overview"])
    m = build(v, decisions={"procedure:Z0": {"decision": "deepen"}, "procedure:Z1": {"decision": "listed_only"}})
    a, b = unfold(m.groups)
    assert set(a) == set(b) and a["marks"]["depth"] == "deepen" and b["marks"]["depth"] == "listed_only"


def test_evidence_basis_head_or_per_line_and_case_speak_name():
    """완료 4 — basis 가 한 값이면 머리 한 번 · 섞이면 줄마다 · 칸 없는 표의 케이스 행에 시술 이름 **값**."""
    same = [row("g_outcome", f"GOU-9000{i}", target_table="procedure", target_code="Z", effect_name=f"e{i}",
                layer=lay()) for i in range(2)]
    g = build(vfrom(same, ["outcome"])).groups[0]
    assert g["marks"]["basis"] == "정리" and all("basis" not in (r.get("marks") or {}) for r in g["rows"])
    mixed = [{**same[0]}, {**same[1], "evidence_kind": "원장"}]
    g2 = build(vfrom(mixed, ["outcome"])).groups[0]
    assert "basis" not in g2["marks"] and [r["marks"]["basis"] for r in g2["rows"]] == ["정리", "원장"]
    case = [row("g_outcome", "GOU-90009", target_table="procedure_case", target_code="ZZK-1", effect_name="e",
                layer=lay())]
    m = build(vfrom(case, ["outcome"]), speak_names={"procedure_case:ZZK-1": "가짜시술"})
    assert unfold(m.groups)[0]["marks"]["speak_name"] == "가짜시술"


async def test_case_row_label_lookup_purpose_label(tmp_path):
    """N-1 — 칸 없는 표의 케이스 행 → 그 케이스의 facets profile 한 번(`purpose` = label · verified 밖)."""
    rows = {"outcome": [row("g_outcome", "GOU-90009", target_table="procedure_case", target_code="ZZK-1",
                            effect_name="e", layer=lay("하위 케이스", "ZZK-1", "가짜케이스", "procedure_case"))]}
    w = await col_b.walk("q", deps(tmp_path, make_hub(resolve=[cand(P1)], facet_rows=rows),
                                   jev_noul(kinds={"outcome": 0.9})))
    lab = [r for r in w.reads if r.call.purpose == "label"]
    assert lab and w.labels == {"procedure_case:ZZK-1": "가짜시술"} and w.end_state == es.OK, w.gate
    assert not {g for r in lab for g in r.gids} & set(w.verified.gids())


async def test_gate_k1_k4_k5_fail_on_synthetic(tmp_path):
    """완료 5 — K1 · K4 · K5 가 각각 실패하는 합성 입력 → 「실행 중단」."""
    from clinic_chat.walk.gate import check

    w = await col_b.walk("q", deps(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul()))
    base = dict(reads=w.reads, verified=w.verified, entries=w.front.entries, decisions=w.depth.decisions,
                ranges=w.front.ranges, kinds=w.front.kinds, material=w.material, planned=w.planned,
                required=w.depth.required)
    assert check(**base)["ok"]
    from clinic_chat.walk.calls import Call

    k1 = check(**{**base, "planned": [*w.planned, Call("facets", {"node": "procedure:ZZP-1", "facet": "outcome"})]})
    assert "K1" in k1["fails"]
    v2 = Verified()
    v2.rows = dict(w.verified.rows)
    v2.add_response({"rows": [row("g_adverse", "GAD-99999", target_table="procedure", target_code="ZZP-1", layer=lay())]},
                    ranges=("adverse",), kinds=("adverse",), depth="breadth", call_no=0)
    k5 = check(**{**base, "verified": v2, "material": build(v2)})
    assert "K5" in k5["fails"]
    m4 = build(w.verified)
    for g in m4.groups:
        g["marks"].pop("basis", None)
        for r in g["rows"]:
            (r.get("marks") or {}).pop("basis", None)
    assert "K4" in check(**{**base, "material": m4})["fails"]


async def test_one_kind_input_smaller_than_b_full(tmp_path, fake_cli):
    """완료 6 — 같은 질문에서 종류 하나만 읽을 때의 llm_input_bytes < 14 종류 전부(B 전체)."""
    from tests.conftest import stream

    bin_, route, _ = fake_cli
    (route / "answer.jsonl").write_text(stream("답"), encoding="utf-8")
    d = deps(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(), claude_cli_bin=bin_)
    one = await col_b.run("q", d, b_on=False)
    full = await col_b.run("q", d, b_on=False, read_kinds=list(KIND_CODES))
    assert one.result["trace"]["llm_input_bytes"] < full.result["trace"]["llm_input_bytes"]


def test_rq1_four_lines_and_kind_value_columns():
    s, inh, low = lay(), lay("물려받음"), lay("하위 제품")
    prof = lambda g, mech, ly: row("g_profile", g, mechanism_name=mech, overview="o", layer=ly)  # noqa: E731
    assert met_by(count_rows([prof("GPF-1", "m", s)], "mechanism")) == "self"
    assert met_by(count_rows([prof("GPF-1", "", s), prof("GPF-2", "m", low), prof("GPF-3", "m", inh)],
                             "mechanism")) == "lower"
    c = count_rows([prof("GPF-1", "", s), prof("GPF-3", "m", inh)], "mechanism")
    assert c == {"self": 0, "lower": 0, "inherited": 1} and met_by(c) == "inherited"
    assert met_by(count_rows([], "outcome")) is None
    assert count_rows([row("g_scope", "GSC-1", role="하위 케이스")], "lineup") == {"self": 0, "lower": 1, "inherited": 0}
