"""WP-3 · WP-5 — 읽기 · RQ1 · 깊이 · 검사 · 종료 상태 · 단계 ⑤ · 답 꼬리(모의 Jev · 모의 허브 · 모의 CLI)."""

from __future__ import annotations

import ast
import random
import re
from pathlib import Path

from clinic_chat.columns import col_b
from clinic_chat.columns.base import Deps
from clinic_chat.config import ROOT
from clinic_chat.walk import end_state as es
from tests.conftest import make_settings, read_log, stream
from tests.fake_hub import row
from tests.fake_jev import FakeJev
from tests.walk_fixture import C1, P1, PR, cand, jev_noul, lay, make_hub, product_rows

GID = re.compile(r"\bG[A-Z]{2}-\d{5}\b")


def deps(tmp_path, hub, noul, **kw):
    s = make_settings(tmp_path, **kw)
    return Deps(s, hub.client(), FakeJev(noul_fn=noul).jev(s))


def req(w, kind):
    return next(i for i in w.depth.required["items"] if i["kind"] == kind)


async def test_ok_procedure_reads_only_read_kinds_no_depth(tmp_path):
    """완료 4 · 6 — 읽기로 하지 않은 종류의 조회 0 · 시술 범위는 ③-1 ~ ③-3 없음(Jev = ① 1 + answer_ready 1).
    0.6: 원리만 yes(개요 no 이하 — KD9 안 돎 · 기본 묶음 개요는 KD11 로 `offered`)."""
    d = deps(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(kinds={"mechanism": 0.9}))
    w = await col_b.walk("q", d)
    assert w.end_state == es.OK and w.gate["ok"], w.gate
    reads = [r for r in w.reads if r.call.purpose == "read"]
    assert {r.call.request.get("facet") for r in reads} == {"profile"}
    assert not w.depth.decisions and len(w.depth.steps) == 1 and d.jev.calls == 2
    assert all(r.ra.complete and r.ra.read == r.ra.total for r in w.reads)


async def test_e2e3_lower_layer_meets_and_jev_cannot_change(tmp_path):
    """완료 1 — 자기 0 · 하위 n · 물려받음 m → 하위 층 행이 verified · met_by = lower · 근거 충족(깊이 Noul 0 이어도)."""
    for other in (0.9, 0.0):
        hub = make_hub(resolve=[cand(PR)], facet_rows=product_rows(),
                       coverage={"product:ZZR-1": {"outcome": (0, 4, 1)}})
        w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"outcome": 0.95}, other=other)))
        r = req(w, "outcome")
        assert (r["self"], r["lower"], r["inherited"], r["met_by"]) == (0, 4, 1, "lower") and w.end_state == es.OK
        assert {f"GOU-9700{i}" for i in range(4)} <= set(w.verified.gids())
        assert r["coverage_mismatch"] is None


async def test_e2e4_inherited_only_meets_with_mark(tmp_path):
    """완료 2 — 자기 0 · 하위 0 · 물려받음 n → met_by = inherited · 근거 충족 · 「물려받은 값으로 채움」 표지.
    원리 칸이 빈칸뿐인 자기 profile 행은 mechanism 의 self 로 안 센다."""
    hub = make_hub(resolve=[cand(PR)], facet_rows=product_rows())
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"mechanism": 0.95})))
    r = req(w, "mechanism")
    assert (r["self"], r["lower"], r["inherited"], r["met_by"]) == (0, 0, 1, "inherited") and w.end_state == es.OK
    from clinic_chat.walk.material import unfold

    inh = [x for x in unfold(w.material.groups) if x["marks"].get("layer") == "물려받음"]
    assert inh and all(x["marks"].get("inherited_fill") for x in inh)
    assert any(g["id"] == "G12" and g["result"] == "pass" for g in w.gate["gates"])


async def test_all_zero_is_exhausted_and_ga_with_judgement(tmp_path):
    """완료 3 — 세 층 0 → 후보 소진 · 원장 확인이 걸리고 판정용 조회도 0 → 원천 미확정 (가)."""
    hub = make_hub(resolve=[cand(PR)], facet_rows={"outcome": []})
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"outcome": 0.95})))
    assert req(w, "outcome")["met_by"] is None and w.end_state == es.EXHAUSTED
    hub2 = make_hub(resolve=[cand(C1)], cc_groups=0, cc_rows0_lck=True)
    w2 = await col_b.walk("q", deps(tmp_path, hub2, jev_noul(kinds={"indication": 0.95})))
    assert w2.end_state == es.UNSETTLED and w2.reason == "(가)"
    assert w2.end_check["needs_judgement"] and w2.end_check["judgement_calls"] >= 1
    judge = [r for r in w2.reads if r.call.purpose == "end_state"]
    assert judge and not {g for r in judge for g in r.gids} & set(w2.verified.gids())  # 판정용 행은 verified 밖


async def test_judgement_rows_make_it_not_ga(tmp_path):
    """(가) 는 「그 범위의 조회 전부」 — 읽지 않은 종류(패키지)에 행이 있으면 (가) 가 아니다(RQ1 넷째 줄)."""
    hub = make_hub(resolve=[cand(C1)], cc_groups=0, cc_rows0_lck=False, facet_rows={
        "package": [row("g_package", "GPK-95100", package_id="ZZG-9", concern_code="ZZC-1")]})

    def cc_lck(p):
        return {"groups": [], "modality_only": [], "unreached": [], "ledger_check": [{"code": "ZZC-1", "lck_ids": ["LCK-9"],
                                                                                    "on": "request"}]}
    hub.routes["concern_candidates"] = cc_lck
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"indication": 0.95})))
    assert w.end_check["needs_judgement"] and not w.end_check["query_zero"]
    assert w.end_state == es.EXHAUSTED and "GPK-95100" not in w.verified.gids()


async def test_q10_upper_hier_rows_do_not_count_for_ga(tmp_path):
    hub = make_hub(resolve=[cand(C1)], cc_groups=0, cc_rows0_lck=True, pkg_upper=3)
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"indication": 0.9, "package": 0.9})))
    assert w.end_state == es.UNSETTLED and w.reason == "(가)"
    assert {g for g in w.verified.gids() if g.startswith("GPK-")} == {"GPK-95000", "GPK-95001", "GPK-95002"}


def test_q10_same_function_for_b_and_key():
    lc = [{"code": "ZZC-1", "lck_ids": ["LCK-900"], "on": "request"}]
    up = [{"gold_table": "g_package", "gid": "GPK-95000", "hier": {"match": "상위"}}]
    direct = [{"gold_table": "g_package", "gid": "GPK-95001", "hier": {"match": "직접"}}]
    assert es.unsettled_rows(up, lc) == "(가)" and es.unsettled_rows(up + direct, lc) is None
    assert es.unsettled_rows([], lc, judgement=direct) is None
    from clinic_chat.evalset import answer_key
    assert answer_key.unsettled_rows is es.unsettled_rows


async def test_concern_profile_per_candidate_no_depth(tmp_path):
    hub = make_hub(resolve=[cand(C1)], cc_groups=40)
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"indication": 0.9})))
    prof = [r for r in w.reads if r.call.meta.get("followup")]
    assert len(prof) == 40 and not w.depth.decisions and len(w.depth.steps) == 1
    assert w.end_state == es.OK


async def test_any_noul_never_drops_read_gids(tmp_path):
    """속성 기반 — 무작위 noul 20 벌(고정 seed): 읽은 gid 는 늘 verified · 재료 줄 수 = verified 수."""
    for seed in range(20):
        rng = random.Random(seed)
        for hub in (make_hub(resolve=[cand(P1)]), make_hub(resolve=[cand(C1)], cc_groups=7)):
            w = await col_b.walk("q", deps(tmp_path, hub, lambda n, i, s, r=rng: r.random()))
            br = {g for r in w.reads if r.call.purpose == "read" for g in r.gids}
            if w.end_state not in col_b.NO_B_ON:
                assert br <= set(w.verified.gids()) and len(w.material.lines) == len(w.verified.rows)


async def test_aborted_paths(tmp_path):
    w = await col_b.walk("q", deps(tmp_path, make_hub(resolve=[cand(C1)], cc_groups=6, fail={"concern_candidates"}),
                                   jev_noul(kinds={"indication": 0.9})))
    assert w.end_state == es.ABORTED and w.groups == []
    s = make_settings(tmp_path)
    w2 = await col_b.walk("q", Deps(s, make_hub(resolve=[cand(P1)]).client(), FakeJev(status=500).jev(s)))
    assert w2.end_state == es.ABORTED and w2.error["kind"] == "jev"
    pair = [row("g_pair", "GPR-91001", from_table="procedure", from_code="ZZP-1", to_table="procedure", to_code="ZZP-9",
                to_name="가짜상대", a_side="from", question_class="보완", a_layer=lay())]
    w3 = await col_b.walk("q", deps(tmp_path, make_hub(resolve=[cand(P1)], pair_rows=pair),
                                    jev_noul(kinds={"combo": 0.9}), walk_max_steps=0))
    assert w3.end_state == es.ABORTED and w3.reason == "C3"


async def test_combo_depth_other_end(tmp_path):
    pair = [row("g_pair", "GPR-91001", from_table="procedure", from_code="ZZP-1", to_table="procedure", to_code="ZZP-9",
                to_name="가짜상대", a_side="from", question_class="보완", same_day="", a_layer=lay())]
    w = await col_b.walk("q", deps(tmp_path, make_hub(resolve=[cand(P1)], pair_rows=pair),
                                   jev_noul(kinds={"combo": 0.9})))
    assert w.depth.decisions["procedure:ZZP-9"]["decision"] == "deepen"
    assert any(r.call.purpose == "depth" for r in w.reads) and w.end_state == es.OK, w.gate


async def test_stage5_three_states_and_tail(tmp_path, fake_cli):
    bin_, route, log = fake_cli
    (route / "answer.jsonl").write_text(stream("상태를 말한 답"), encoding="utf-8")
    lst = [{"gold_table": "g_entry", "gid": "GEN-99001", **P1}]
    cases = [(make_hub(resolve=[], list_mode=lst), jev_noul(kinds={}, gap=0.9), es.OUT),
             (make_hub(resolve=[], list_mode=lst), jev_noul(kinds={"overview": 0.9}, entry=0.5), es.NO_ENTRY)]
    for hub, nl, want in cases:
        out = await col_b.run("q", deps(tmp_path, hub, nl, claude_cli_bin=bin_))
        assert out.result["end_state"] == want and out.result["answer"] == "상태를 말한 답"
        assert out.extra["b_on"] is None and out.result["trace"]["answer_lines"] is None
        prompt = read_log(log)[-1]["stdin"]
        assert want in prompt and not GID.search(prompt.split("## 질문")[0])


async def test_answer_tail_split_and_material_has_no_ids(tmp_path, fake_cli):
    """WP-5 완료 — 꼬리 있는 답 → answer 에 꼬리 없음 · answer_lines ⊂ verified_lines.n / 깨진 꼬리 → null · 답 그대로."""
    bin_, route, log = fake_cli
    (route / "answer.jsonl").write_text(stream(f"첫 줄\n둘째 줄\n{col_b.TAIL_MARK}\n1: 1\n2: 1,2"), encoding="utf-8")
    out = await col_b.run("q", deps(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(), claude_cli_bin=bin_), b_on=False)
    tr = out.result["trace"]
    assert out.result["answer"] == "첫 줄\n둘째 줄" and col_b.TAIL_MARK not in out.result["answer"]
    ns = {x["n"] for x in tr["verified_lines"]}
    assert tr["answer_lines"] == [{"answer_line": 1, "rows": [1]}, {"answer_line": 2, "rows": [1, 2]}]
    assert {n for a in tr["answer_lines"] for n in a["rows"]} <= ns and tr["answer_tail_chars"] > 0
    mat = read_log(log)[-1]["stdin"].split("## 질문")[0]
    assert not GID.search(mat) and "silver_" not in mat and "price_mid_raw" not in mat and "evidence_kind" not in mat
    assert not re.search(r"\bg_[a-z]", mat)  # WORK-008 WP-5 N-5 — gold 표 이름(`g_…`)은 LLM 에 안 간다(기록에만)
    assert body_and_tail("답\n<<<근거>>>\n엉망") == ("답", None)


def body_and_tail(t):
    b, al, _ = col_b.split_tail(t)
    return b, al


def test_tail_filter_hides_marker_across_deltas():
    f = col_b._TailFilter()
    outs = [f({"event": "delta", "text": x}) for x in ("답 본문", "<<<근", "거>>>\n1: 2", "더")]
    shown = "".join(o["text"] for o in outs if o)
    assert shown == "답 본문" and f({"event": "step"}) == {"event": "step"}


async def test_fold_too_large_is_aborted_ft5(tmp_path, fake_cli):
    bin_, route, log = fake_cli
    (route / "answer.jsonl").write_text(stream("상태를 말한 답"), encoding="utf-8")
    d = deps(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(), claude_cli_bin=bin_, answer_input_max_tokens=50)
    out = await col_b.run("q", d)
    assert out.result["end_state"] == es.ABORTED and out.result["trace"]["end_reason"] == "FT5"
    assert out.extra["b_on"] is None and out.result["trace"]["verified_gids"] == []
    assert "(없음)" in read_log(log)[-1]["stdin"]


async def test_b_on_same_material_and_no_tools(tmp_path, fake_cli):
    bin_, route, log = fake_cli
    (route / "answer.jsonl").write_text(stream("답"), encoding="utf-8")
    await col_b.run("q", deps(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(), claude_cli_bin=bin_))
    calls = [x for x in read_log(log) if "-p" in x["argv"]]
    assert len(calls) == 2 and calls[0]["stdin"] == calls[1]["stdin"]
    assert all("--mcp-config" not in c["argv"] for c in calls)


def test_no_end_state_answer_sentences_in_code():
    banned = ("다루지 않는다고", "되묻", "확인하지 못했", "원장 확인이 필요")
    for p in (Path(ROOT) / "clinic_chat").rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) > 3:
                assert not any(b in n.value for b in banned) or "\n" in n.value, f"{p.name}: {n.value[:40]}"


S1 = {"entity_table": "site", "entity_code": "ZZS-1", "entity_name": "가짜부위", "text": "가짜부위"}
S2 = {"entity_table": "site", "entity_code": "ZZS-2", "entity_name": "가짜다른부위", "text": "가짜다른부위"}


async def test_w1_attr_only_unapplicable_kind_is_not_aborted(tmp_path):
    """W-1 재현 — 부위 대상 + 회복 yes: 0.5.0 판은 kinds [] · 조회 0 · gate:K3 실행 중단 → 이제 KD6 기본 묶음."""
    w = await col_b.walk("q", deps(tmp_path, make_hub(resolve=[cand(S1)]), jev_noul(kinds={"recovery": 0.9})))
    assert w.end_state != es.ABORTED and w.gate["K"]["K3"]["ok"], w.gate
    assert {r.call.request.get("facet") for r in w.reads if r.call.purpose == "read"} == {"profile", "indication"}


def test_w2_two_sites_split_per_site():
    """W-2 — 부위 입구 둘이면 부위마다 조회(사건 · 조건과 같은 식) · `concern_candidates` 의 `site` 도."""
    from clinic_chat.walk.calls import Entry, plan_calls

    p, c = Entry("procedure", "ZZP-1", "가짜시술"), Entry("concern", "ZZC-1", "가짜고민")
    s1, s2 = Entry("site", "ZZS-1", "가짜부위"), Entry("site", "ZZS-2", "가짜다른부위")
    calls = plan_calls([p, s1, s2], ["overview", "adverse", "recovery"])
    prof = [x.request for x in calls if x.request.get("facet") == "profile"]
    assert [q["filters"] for q in prof] == [{"site_name": "가짜부위"}, {"site_name": "가짜다른부위"}]
    assert len([x for x in calls if x.request.get("facet") in ("adverse", "adverse_detail")]) == 4
    assert [x.request.get("filters") for x in calls if x.request.get("facet") == "recovery"] == [None]  # 필터 자리 없음
    a1 = Entry("adverse", "ZZA-1", "가짜사건")
    adv = [x.request["filters"] for x in plan_calls([p, a1, s1, s2], ["adverse"]) if x.request.get("facet") == "adverse"]
    assert adv == [{"adverse_code": "ZZA-1", "site_name": "가짜부위"}, {"adverse_code": "ZZA-1", "site_name": "가짜다른부위"}]
    cc = [x.request for x in plan_calls([c, s1, s2], ["indication"]) if x.tool == "concern_candidates"]
    assert cc == [{"concern_code": "ZZC-1", "site": "가짜부위"}, {"concern_code": "ZZC-1", "site": "가짜다른부위"}]
    assert plan_calls([p, s1], ["overview"])[0].request["filters"] == {"site_name": "가짜부위"}  # 하나면 그대로


async def test_w2_both_sites_referenced_and_unused_reason(tmp_path):
    """W-2 — 두 부위 다 조회에 쓰여 unused 0 · 필터 자리 없는 종류만 읽으면 까닭은 `br1_no_site_filter_on_read_kinds`."""
    hub = make_hub(resolve=[cand(P1), cand(S1), cand(S2)])
    w = await col_b.walk("q", deps(tmp_path, hub, jev_noul(kinds={"overview": 0.9})))
    assert w.gate["unused_entries"] == []
    w2 = await col_b.walk("q", deps(tmp_path, make_hub(resolve=[cand(P1), cand(S1)]), jev_noul(kinds={"combo": 0.9})))
    assert w2.gate["unused_entries"] == [{"id": "site:ZZS-1", "why": "br1_no_site_filter_on_read_kinds"}]
