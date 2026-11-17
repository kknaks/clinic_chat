"""앱 0.5.2 수정 1 — 덮인 조각 규칙(_RESUME §2 11-17) · 코드 검수 v052 W-1 ~ W-4.

모의 허브 · 가짜 코드(ZZ…) · 가짜 이름(「가짜…」)만 쓴다. 응답은 손으로 적은 꼴뿐(규칙 계산 없음).
"""

from __future__ import annotations

from clinic_chat.clients.hub_rest import ReadAll
from clinic_chat.evalset import answer_key as ak
from clinic_chat.walk.breadth import Read
from clinic_chat.walk.calls import Call, Entry, plan_calls
from clinic_chat.walk.front import front
from clinic_chat.walk.required import call_side, required_status
from clinic_chat.walk.verified import Verified
from tests.conftest import make_settings
from tests.fake_hub import row
from tests.fake_jev import FakeJev
from tests.walk_fixture import cand, jev_noul, make_hub

# 「가짜볼주름」 = 긴 고민 이름 · 그 안에 든 「가짜주름」(고민) · 「가짜볼」(부위)
CB = {"entity_table": "concern", "entity_code": "ZZC-12", "entity_name": "가짜볼 주름", "text": "가짜볼주름"}
CW = {"entity_table": "concern", "entity_code": "ZZC-1", "entity_name": "가짜주름", "text": "가짜주름"}
SB = {"entity_table": "site", "entity_code": "ZZS-51", "entity_name": "가짜볼", "text": "가짜볼"}
SF = {"entity_table": "site", "entity_code": "ZZS-14", "entity_name": "가짜이마", "text": "가짜이마"}
LONG = "가짜볼주름"


def _res_covered():
    return [cand(CB, span=LONG), cand(CW, span="주름", start=3, covered_by=LONG),
            cand(SB, span="가짜볼", covered_by=LONG)]


async def _front(tmp_path, res, noul):
    s = make_settings(tmp_path)
    return await front("가짜 질문", make_hub(resolve=res).client(), FakeJev(noul_fn=noul).jev(s), s)


def _cc(calls):
    return [c.request for c in calls if c.tool == "concern_candidates"]


def _filtered(calls):
    return [c.request for c in calls if c.request.get("site") or c.request.get("filters")]


# ───── F-1 덮인 조각 ─────

async def test_covered_between_dropped_and_no_site_filter(tmp_path):
    """긴 이름 꼴 — 덮인 고민 0.56 · 덮인 부위 0.37(사이 값) → 둘 다 dropped(noul 함께) · 덮은 고민 조회에 거르개 0."""
    vals = {0: 0.94, 1: 0.56, 2: 0.37}
    f = await _front(tmp_path, _res_covered(), jev_noul(target=lambda i, st: vals[i], kinds={"indication": 0.9}))
    dec = {t["id"]: t["decision"] for t in f.trace_front["targets"]}
    assert dec == {"concern:ZZC-12": "target", "concern:ZZC-1": "dropped", "site:ZZS-51": "dropped"}
    assert {(d["id"], d["noul"]) for d in f.trace_front["dropped"]} == {("concern:ZZC-1", 0.56), ("site:ZZS-51", 0.37)}
    calls = plan_calls(f.entries, f.kinds)
    assert _cc(calls) == [{"concern_code": "ZZC-12"}] and _filtered(calls) == []


async def test_covered_yes_is_target_but_not_filter_of_its_cover(tmp_path):
    """덮인 조각 yes → 대상. 그러나 덮은 고민의 조회는 그 안 부위로 좁히지 않는다."""
    f = await _front(tmp_path, _res_covered(), jev_noul(target=0.9, kinds={"indication": 0.9}))
    assert {e.node for e in f.entries} == {"concern:ZZC-12", "concern:ZZC-1", "site:ZZS-51"}
    assert not f.trace_front["dropped"]
    cc = _cc(plan_calls(f.entries, f.kinds))
    assert {"concern_code": "ZZC-12"} in cc and {"concern_code": "ZZC-12", "site": "가짜볼"} not in cc
    # 덮은 이름이 아닌 고민(같은 긴 이름 속 다른 조각)은 부위 거르개 그대로
    assert {"concern_code": "ZZC-1", "site": "가짜볼"} in cc


async def test_separate_names_keep_site_filter_regression(tmp_path):
    """떨어진 이름 꼴 회귀 — 「가짜이마에 가짜주름」: 둘 다 덮이지 않음 · yes → 부위 거른 조회 하나뿐(HG3 꼴 그대로)."""
    res = [cand(CW, span="가짜주름", start=5), cand(SF, span="가짜이마")]
    f = await _front(tmp_path, res, jev_noul(target=0.9, kinds={"indication": 0.9}))
    assert _cc(plan_calls(f.entries, f.kinds)) == [{"concern_code": "ZZC-1", "site": "가짜이마"}]


async def test_uncovered_between_site_reads_both(tmp_path):
    """W-2 — 덮이지 않은 사이 값 부위(TG2)는 AND 거르개로 폭을 줄이지 않는다: 거른 조회 + 안 거른 조회."""
    res = [cand(CW, span="가짜주름", start=5), cand(SF, span="가짜이마")]
    f = await _front(tmp_path, res, jev_noul(target=lambda i, st: 0.9 if i == 0 else 0.5, kinds={"indication": 0.9}))
    assert next(e for e in f.entries if e.node == "site:ZZS-14").by == "uncertain_kept"
    cc = _cc(plan_calls(f.entries, f.kinds))
    assert cc == [{"concern_code": "ZZC-1"}, {"concern_code": "ZZC-1", "site": "가짜이마"}]


def test_uncertain_adverse_filter_reads_both():
    """W-2 — 사건 거르개도 같다: 사이 값 사건 입구면 거른 조회 + 안 거른 조회."""
    t = Entry("procedure", "ZZP-1", "가짜시술", "target")
    a = Entry("adverse", "ZZA-1", "가짜사건", "uncertain_kept", via="TG2")
    reqs = [c.request for c in plan_calls([t, a], ["adverse"]) if c.request.get("facet") == "adverse"]
    assert [r.get("filters") for r in reqs] == [{"adverse_code": "ZZA-1"}, None]
    a2 = Entry("adverse", "ZZA-1", "가짜사건", "target")
    reqs2 = [c.request for c in plan_calls([t, a2], ["adverse"]) if c.request.get("facet") == "adverse"]
    assert [r.get("filters") for r in reqs2] == [{"adverse_code": "ZZA-1"}]


# ───── W-1 필터로 쓴 사건 입구도 대상 매칭 — B 와 키가 같은 함수 ─────

def _ra(call: Call, rows: list[dict], lc=None) -> ReadAll:
    return ReadAll(call.tool, call.request, total=len(rows), pages=1, read=len(rows), complete=True,
                   items=rows, ledger_check=lc or [])


def test_filter_entry_counted_for_required_and_key():
    t = Entry("modality", "ZZM-2", "가짜계열", "target")
    a = Entry("adverse", "ZZA-1", "가짜사건", "target")
    calls = plan_calls([t, a], ["adverse"])
    adv = next(c for c in calls if c.request.get("facet") == "adverse")
    assert adv.request["filters"] == {"adverse_code": "ZZA-1"} and call_side(adv.request, "adverse:ZZA-1") == "a"
    rows = [row("g_adverse", "GAD-99001", target_table="modality", target_code="ZZM-2", adverse_code="ZZA-1",
                layer={"role": "자기"})]
    reads = [Read(adv, _ra(adv, rows), ["GAD-99001"])]
    st = required_status(["adverse"], ["modality:ZZM-2", "adverse:ZZA-1"], reads)
    by = {i["target"]: i for i in st["items"]}
    assert by["adverse:ZZA-1"]["met_by"] == "self" and st["ok"]
    assert by["adverse:ZZA-1"]["coverage_mismatch"] is None  # 좁힘 조회 — 대조 안 함
    spec = {"entries": [{"table": "modality", "code": "ZZM-2"}, {"table": "adverse", "code": "ZZA-1"}],
            "expected_kinds": ["adverse"]}
    km = {(i["target"], i["kind"]): i["met_by"] for i in ak.kinds_met(spec, [{"call": adv, "rows": rows}])}
    assert km == {("modality:ZZM-2", "adverse"): "self", ("adverse:ZZA-1", "adverse"): "self"}


# ───── W-3 ledger_check 가 trace 에 ─────

def test_breadth_summary_has_ledger_check():
    c = Call("concern_candidates", {"concern_code": "ZZC-1"})
    lc = [{"code": "ZZC-1", "lck_ids": ["LCK-900"], "on": "request"}]
    assert Read(c, _ra(c, [], lc), []).trace()["ledger_check"] == lc


# ───── W-4 같은 gid 가 두 조회에서 — 표지 합침(차례 무관) ─────

def _r():
    return row("g_indication", "GIN-99500", candidate_table="procedure", candidate_code="ZZP-1", layer={"role": "자기"})


def test_marks_merge_regardless_of_order():
    plain = {"rows": [_r()]}
    upper = {"inherited_from": {"concern_code": "ZZC-0", "steps": 1}, "rows": [_r()]}
    sm = {"rows": [{**_r(), "site_match": [{"by": "concern_site", "csi_ids": ["CSI-9001"]}]}]}
    sm2 = {"rows": [{**_r(), "site_match": [{"by": "column"}]}]}
    for order in ([plain, upper, sm, sm2], [sm2, sm, upper, plain]):
        v = Verified()
        for i, resp in enumerate(order):
            v.add_response(resp, ranges=("concern",), kinds=("indication",), depth="breadth", call_no=i)
        r = v.rows["GIN-99500"]
        assert r.marks["inherited_from"] == {"concern_code": "ZZC-0", "steps": 1}
        assert {x["by"] for x in r.row["site_match"]} == {"concern_site", "column"}
        assert r.marks["site_match"] == r.row["site_match"]
