"""case3 Jev 걸음(SPEC-006 v2.4 §3-4) — 모의 Jev · 모의 허브(peek · expand · SPEC-005 v3.3 §5-16 짧은 fact 꼴) · 모의 Anthropic.

걸음 1 이웃 먼저(순서 표 · 단위 규칙 · 앞 4 밖 목록) · 섹션 상한(의도별) · 「충분」 없음 · 대기열 · S 로 돌아옴 · by_unit ·
예산(미리 거름 · 넘는 호출만 뺌) · 바이트 셈 compact · 걸음 경로 intent 없음 · ⑤ cut 줄 원문 · §3-4-7 넘김 꼴 · 되돌아가기.
"""

from __future__ import annotations

import json
from itertools import pairwise

from clinic_chat.cases import case3
from clinic_chat.timing import CaseTimer, Clock
from tests.fake_anthropic import FakeAnthropic, Turn
from tests.fake_jev import FakeJev

U140, U102 = "unit_procedure:U5-140", "unit_procedure:U5-102"
WALK_KEYS = {"start_node", "intent", "order_kinds", "order_table_sha", "list", "by_unit_queued", "step1_options",
             "bytes_mismatch", "steps", "queue_left", "stop", "steps_n", "bytes_total", "budget_hit", "neighbor_reached",
             "full", "more", "fallback", "fallback_reason", "jev_calls", "jev_ms_total", "hub_calls", "hub_ms_total",
             "fact_ids"}


def timer() -> CaseTimer:
    c = Clock()
    c.start()
    return CaseTimer(c)


def also(*names):
    """짝 Noul — instructions 에 이름이 든 선택지만 0.9(나머지 0.1) · 「충분」 은 기본(0.1)."""
    def fn(name, instr):
        if name.startswith(("also:", "nb_also:", "sec_also:")):
            return 0.9 if any(n in instr for n in names) else 0.1
        return None
    return fn


# v2.4 규칙만 보는 시험 — 걷기 고침 2(필수 묶음 · 고정 섹션 · 「충분」 늦추기)를 끔(아래 fix2 시험이 켜고 봄)
V24 = {"walk_required_sections_csv": "", "walk_fixed_sections_by_intent": "", "walk_enough_min": 0.5,
       "walk_enough_bytes_ratio": 0.0, "walk_jev_sections": True, "walk_base_bundle": False, "walk_fill": False,
       "walk_safety_budget": 0}
# 걷기 고침 2 판(필수 묶음 · 고정 섹션 · 「충분」 50%) — 고침 3(이웃만 · 의도 묶음 · 채우기)은 끔
FIX2 = {"walk_enough_bytes_ratio": 0.5, "walk_jev_sections": True, "walk_base_bundle": False, "walk_fill": False,
        "walk_safety_budget": 0}
PROFILES = {"v24": V24, "fix2": FIX2, "fix3": {"walk_safety_budget": 0}, "fix4": {}}


async def run(deps_factory, q, jev: FakeJev, seed=0, fix2=False, profile=None, **kw):
    fa = FakeAnthropic([Turn(text=["답변"])])
    prof = PROFILES[profile or ("fix2" if fix2 else "v24")]
    deps = deps_factory(anthropic=fa, **{**prof, **kw})
    deps.jev = jev.client(deps.settings)
    deps.vocab = await deps.hub_rest.vocab()
    r = await case3.run(deps, q, timer(), lambda t: None, seed=seed)
    return r, fa, deps


def hub_calls(hub):
    return [(t, a) for via, t, a in hub.calls if via == "gold-rest" and t != "vocab"]


def sonnet_input(fa) -> dict:
    user = fa.calls[0]["messages"][0]["content"]
    return json.loads(user.split("[허브 결과 — Jev 걸음으로 모은 것]\n", 1)[1])


Q = "소프웨이브 끝나고 사우나 가도 돼요?"
CUT = f"{U140}#aftercare#fixture[2]"  # 40자 넘는 줄(cut)


def jev_qs(jev, i):
    return jev.requests[i]["questions"]


async def test_first_step_neighbors_first_then_queue(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": U140, "nb": U102, "sec": "section:aftercare", "pick": "section:overview"},
                  nouls={"enough": [0.9]})
    r, fa, _ = await run(deps_factory, Q, jev)
    assert r["error"] is None, r["error"]
    w = r["trace"]["walk"]
    assert set(w) >= WALK_KEYS and len(w["order_table_sha"]) == 12
    table, _ = case3.order_table(r and _settings(deps_factory))
    assert w["order_kinds"] == table["table"]["_all"]["kinds"]  # I-03 = 전체 표
    s1, s2 = w["steps"]
    assert (s1["phase"], s2["phase"], s2["node"]) == ("first", "queue", U102)
    # 걸음 1 선택지 = 표 edge kind 이웃(treats · key_family · procedure_relation) + 섹션 — 표 밖 종류(requires_check_unit 등) 없음
    nbs = [x for x in w["step1_options"] if not x.startswith("section:")]
    assert set(nbs) == {"concern:P3", "family:M9", U102}
    assert {x for x in w["step1_options"] if x.startswith("section:")} >= {"section:aftercare", "section:overview"}
    q1 = jev_qs(jev, 1)
    assert "nb" in q1 and "sec" in q1 and "enough" not in q1 and "없음(충분)" not in q1["nb"]["criteria"]
    assert "없음" in q1["sec"]["criteria"] and any(k.startswith("sec_also:") for k in q1)  # I-03 섹션 상한 2
    assert any("fact " in d for d in q1["nb"]["criteria"].values()) and any("B" in d for d in q1["sec"]["criteria"].values())
    # 이웃 slim 먼저 → 섹션
    calls = s1["expand"]["calls"]
    assert [c["input"] for c in calls] == [{"neighbors": [U102]}, {"sections": ["aftercare"]}]
    assert s1["jev"]["picked_neighbors"] == [U102] and s1["jev"]["picked_sections"] == ["aftercare"]
    assert s2["jev"]["enough"] == 0.9 and "expand" not in s2 and w["stop"] == "enough"  # 걸음 2 · fact ≥ 1 · 충분
    assert w["bytes_total"] == sum(c["bytes"] for c in calls) and w["bytes_mismatch"] == 0
    # 걸음 경로 허브 호출에는 intent 없음 · 섹션 호출 본문 = {"sections": [s]} 만
    walk_calls = [(t, a) for t, a in hub_calls(hub) if t in ("peek", "expand")]
    assert all(a.get("intent") is None for _, a in walk_calls)
    assert ("expand", {"id": U140, "sections": ["aftercare"]}) in walk_calls
    # ⑤ cut 줄 원문 — 출처 노드마다 1회
    assert w["full"]["calls"] == 1 and w["full"]["fact_ids"] == [CUT] and w["full"]["cut_unfetched_ids"] == []
    assert ("expand", {"id": U140, "fact_ids": [CUT]}) in walk_calls
    names = [x["name"] for x in r["timing"]["stages"]]
    assert names == ["hub_resolve", "jev#0", "hub_peek#1", "jev#1", "hub_expand#1", "hub_expand#1-2", "hub_peek#2", "jev#2",
                     "hub_full#1", "claude"]
    p = sonnet_input(fa)
    assert [f["rank"] for f in p["facts"]] == list(range(1, 4)) and [f["id"] for f in p["facts"]] == w["fact_ids"]
    cut_row = next(f for f in p["facts"] if f["id"] == CUT)
    assert "…" not in cut_row["text"] and "quotable" not in cut_row and cut_row["source"]["id"] == U140
    assert set(p["claude_contract"]["allowed_fact_ids"]) == set(w["fact_ids"])
    assert p["claude_contract"]["rule"] and set(p["safety"]) == {U140, U102}
    assert [n["id"] for n in p["neighbors"]] == [U102]
    assert "hub" not in r["trace"]


def _settings(deps_factory):
    return deps_factory().settings


async def test_bytes_compact_equals_peek(deps_factory, hub):
    """앱 바이트 셈 = compact · peek sections[].bytes(모의 = 실제 expand 직렬화)와 차 0 · intent 없이."""
    from clinic_chat.clients.hub_rest import HubRest
    from tests.fake_hub import REST_URL

    deps = deps_factory()
    rest: HubRest = deps.hub_rest
    for node in (U140, "family:M5", "product:M9-1-10"):
        p = await rest.peek(node)
        for sec in p["sections"]:
            ex = await rest.expand(node, sections=[sec["name"]])
            assert case3._compact(ex) == sec["bytes"], (node, sec["name"])
    assert REST_URL


async def test_line_cut_and_full_shape(deps_factory, hub):
    deps = deps_factory()
    ex = await deps.hub_rest.expand(U140, sections=["aftercare"])
    rows = {f["id"]: f for f in ex["facts"]}
    assert rows[CUT]["cut"] is True and len(rows[CUT]["line"]) == 40 and rows[CUT]["line"].endswith("…")
    assert "cut" not in rows[f"{U140}#aftercare#fixture[0]"] and "text" not in rows[CUT]
    full = await deps.hub_rest.expand(U140, fact_ids=[CUT])
    assert full["facts"][0]["text"].endswith("바른다") and ex["claude_contract"]["allowed"] == "facts_uncut"


async def test_list_call_by_unit_and_unit_link(deps_factory, hub):
    """계열 S(단위 6 · key_family n 6 > 앞 4) — 목록 호출 1(예산 밖) · 단위 6 이 선택지 · 섹션 by_unit 단위가 그 section 으로
    대기열에 들고 그 걸음은 Jev 섹션 고름 없음."""
    jev = FakeJev(intent="I-04", choices={"start": "family:M5", "nb": "unit_procedure:U5-506", "sec": "section:aftercare"},
                  nouls={"enough": [0.1]})
    r, fa, _ = await run(deps_factory, "피코 토닝 부작용", jev, walk_max_steps=4)
    w = r["trace"]["walk"]
    assert w["list"]["calls"] == 1 and w["list"]["kinds"] == ["key_family"] and w["list"]["bytes"] > 0
    nbs = {x for x in w["step1_options"] if not x.startswith("section:")}
    assert {f"unit_procedure:U5-50{k}" for k in range(1, 7)} <= nbs  # 앞 4 밖 5 · 6 포함
    assert w["bytes_total"] == sum(c["bytes"] for st in w["steps"] for c in (st.get("expand") or {}).get("calls", [])
                                   if not c.get("excluded"))  # 목록 바이트는 예산 밖
    assert [x["unit"] for x in w["by_unit_queued"]] == [f"unit_procedure:U5-50{k}" for k in range(1, 5)]
    assert all(x["section"] == "aftercare" for x in w["by_unit_queued"])
    s2, s3 = w["steps"][1], w["steps"][2]
    assert s2["node"] == "unit_procedure:U5-506" and s2["phase"] == "queue"  # 고른 이웃이 먼저(대기열 순)
    assert s3["node"] == "unit_procedure:U5-501" and s3["by_unit_section"] == "aftercare" and s3["jev"] is None
    assert s3["expand"]["calls"][0]["input"] == {"sections": ["aftercare"]} and w["neighbor_reached"] >= 1
    assert "hub_list#1-1" in [x["name"] for x in r["timing"]["stages"]]
    srcs = {f["source"]["id"]: f["source"].get("edge") for f in sonnet_input(fa)["facts"]}
    assert srcs["unit_procedure:U5-501"] == "key_family" and srcs["family:M5"] is None


async def test_product_start_adds_product_unit(deps_factory, hub):
    """I-04 표에 product_unit 없음 — 제품 S 면 늘 더함."""
    jev = FakeJev(intent="I-04", choices={"start": "product:M9-1-10", "nb": U140, "sec": "없음"}, nouls={"enough": [0.9]})
    r, _, _ = await run(deps_factory, "10THERA 부작용", jev)
    w = r["trace"]["walk"]
    assert w["order_kinds"][-1] == "product_unit" and "product_unit" not in case3.order_table(_settings(deps_factory))[0][
        "table"]["I-04"]["kinds"]
    assert {U140, U102} <= set(w["step1_options"])


async def test_fill_with_next_kind_when_table_kinds_absent(deps_factory, hub):
    """확인 조건 S — I-04 표 종류(key_family · product_family · procedure_relation) 이웃 없음 → peek 종류 순 다음 종류로 채움."""
    jev = FakeJev(intent="I-04", choices={"start": "check_condition:C-PREG", "nb": U140, "pick": "section:overview"},
                  nouls={"enough": [0.9]})
    r, _, _ = await run(deps_factory, "임신 중 부작용", jev)
    w = r["trace"]["walk"]
    assert w["order_fill"] == "requires_check_unit" and U140 in w["step1_options"]
    assert w["steps"][0]["jev"]["sec_options_n"] == 0  # 확인 조건 노드 = 섹션 없음 → 이웃만
    assert w["steps"][1]["node"] == U140 and w["fact_ids"]


async def test_enough_needs_one_fact(deps_factory, hub):
    """「충분」 ≥ 0.5 라도 fact 0 이면 안 멈춤 — 걸음 1 섹션 「없음」 · 걸음 2 충분 0.9 → 펼침 계속."""
    jev = FakeJev(intent="I-01", choices={"start": U140, "nb": U102, "sec": "없음", "pick": "section:overview"},
                  nouls={"enough": [0.9, 0.9]})
    r, _, _ = await run(deps_factory, Q, jev)
    w = r["trace"]["walk"]
    s2 = w["steps"][1]
    assert s2["jev"]["enough"] == 0.9 and s2["expand"]["sections"] == ["overview"]  # fact 0 → 안 멈춤
    assert w["steps"][2]["jev"]["enough"] == 0.9 and w["stop"] == "enough" and w["neighbor_reached"] == 1


async def test_return_to_start_reuses_peek(deps_factory, hub):
    jev = FakeJev(intent="I-01", choices={"start": U140, "nb": U102, "sec": "section:aftercare",
                                          "pick": ["section:overview", "section:method"]}, nouls={"enough": [0.1, 0.1]})
    r, _, _ = await run(deps_factory, Q, jev)
    w = r["trace"]["walk"]
    s3 = w["steps"][2]
    assert (s3["phase"], s3["node"], s3["peek_reused"]) == ("return", U140, True)
    assert "section:aftercare" not in jev_qs(jev, 3)["pick"]["criteria"]  # 받은 섹션은 다시 안 뜸
    assert [t for t, a in hub_calls(hub) if t == "peek"] == ["peek", "peek"]  # S 로 돌아올 때 peek 호출 없음
    assert s3["expand"]["sections"] == ["method"]


async def test_budget_prefilter_and_skip_over(deps_factory, hub):
    """남은 예산 넘는 섹션은 선택지에 없음 · 실제 바이트가 넘는 호출은 그것만 빼고 다음 섹션을 담음(멈추지 않음)."""
    deps = deps_factory()
    p = await deps.hub_rest.peek(U140)
    by = {x["name"]: x["bytes"] for x in p["sections"]}
    slim = case3._compact(await deps.hub_rest.expand(U140, neighbors=[U102]))
    budget = slim + by["overview"] + 5  # aftercare 는 혼자 들어가나(≤ 예산) slim 뒤엔 넘음 · overview 는 들어감
    assert by["aftercare"] <= budget < slim + by["aftercare"]
    jev = FakeJev(intent="I-03", choices={"start": U140, "nb": U102, "sec": "section:aftercare"},
                  noul_fn=also("overview"), nouls={"enough": [0.9]})
    r, _, _ = await run(deps_factory, Q, jev, walk_byte_budget=budget)
    w = r["trace"]["walk"]
    s1 = w["steps"][0]
    big = [n for n, b in by.items() if b > budget]
    assert set(s1["jev"]["filtered_by_budget"]) == set(big)
    assert all("section:" + n not in jev_qs(jev, 1)["sec"]["criteria"] for n in big)
    assert s1["expand"]["excluded"] == ["aftercare"] and s1["expand"]["sections"] == ["overview"]
    assert w["budget_hit"] is True and w["stop"] != "budget" and w["bytes_total"] <= budget


async def test_bytes_mismatch_counted(deps_factory, hub, monkeypatch):
    from tests.fake_hub import gold

    orig = gold._bytes
    monkeypatch.setattr(gold, "_bytes", lambda o: orig(o) + 1)
    jev = FakeJev(intent="I-03", choices={"start": U140, "nb": U102, "sec": "section:aftercare"}, nouls={"enough": [0.9]})
    r, _, _ = await run(deps_factory, Q, jev)
    c = r["trace"]["walk"]["steps"][0]["expand"]["calls"][1]
    assert r["trace"]["walk"]["bytes_mismatch"] == 1 and c["peek_bytes"] == c["bytes"] + 1


async def test_full_budget_unfetched_cut_not_quotable(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": U140, "nb": U102, "sec": "section:aftercare"}, nouls={"enough": [0.9]})
    r, fa, _ = await run(deps_factory, Q, jev, walk_full_byte_budget=50)
    w = r["trace"]["walk"]
    assert w["full"]["cut_unfetched_ids"] == [CUT] and w["full"]["over_budget"] is True
    p = sonnet_input(fa)
    row = next(f for f in p["facts"] if f["id"] == CUT)
    assert row["cut"] is True and row["quotable"] is False and row["line"].endswith("…") and "text" not in row
    assert CUT not in p["claude_contract"]["allowed_fact_ids"] and len(p["claude_contract"]["allowed_fact_ids"]) == 2


async def test_uncut_lines_not_fetched(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": U140, "nb": U102, "sec": "section:overview"}, nouls={"enough": [0.9]})
    r, _, _ = await run(deps_factory, Q, jev)
    assert r["trace"]["walk"]["full"]["calls"] == 0
    assert "hub_full#1" not in [x["name"] for x in r["timing"]["stages"]]


async def test_stop_steps(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": U140, "nb": U102, "sec": "section:aftercare"})
    r, _, _ = await run(deps_factory, Q, jev, walk_max_steps=1)
    w = r["trace"]["walk"]
    assert (w["stop"], w["steps_n"], w["fallback"]) == ("steps", 1, False) and len(w["fact_ids"]) == 3
    assert w["queue_left"] == [U102]


async def test_fallback_no_start(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": "해당 없음"})
    r, fa, _ = await run(deps_factory, Q, jev)
    w = r["trace"]["walk"]
    assert (w["fallback"], w["fallback_reason"], w["steps_n"]) == (True, "no_start", 0)
    assert r["trace"]["hub"]["tool"] == "procedure_card" and r["trace"]["hub"]["path"] == "I-03"
    assert [s["name"] for s in r["timing"]["stages"]] == ["hub_resolve", "jev#0", "jev", "hub_path", "claude"]
    assert "[허브 결과]" in fa.calls[0]["messages"][0]["content"]
    assert r["error"] is None


async def test_fallback_peek_failed(deps_factory, hub):
    hub.fail_tools.add("peek")
    r, _, _ = await run(deps_factory, Q, FakeJev(intent="I-03", choices={"start": U140}))
    w = r["trace"]["walk"]
    assert (w["fallback"], w["fallback_reason"]) == (True, "peek_failed") and "error" in w["steps"][0]
    assert r["trace"]["hub"]["tool"] and r["error"] is None


async def test_fallback_no_facts(deps_factory, hub):
    # 고민 S = 섹션 없음 · 이웃 단위 걸음에서 「없음(충분)」 · S 로 돌아와도 「없음(충분)」 → 모은 fact 0
    jev = FakeJev(intent="I-01", choices={"start": "concern:P3", "nb": U140, "pick": "없음(충분)"})
    r, _, _ = await run(deps_factory, "처짐이 고민이에요", jev)
    w = r["trace"]["walk"]
    assert (w["fallback"], w["fallback_reason"]) == (True, "no_facts") and w["steps_n"] >= 2
    assert r["trace"]["hub"]["tool"] == "recommend"


async def test_price_intent_no_walk(deps_factory, hub):
    r, _, _ = await run(deps_factory, "소프웨이브 얼마예요?", FakeJev(intent="I-12", choices={"start": U140}))
    w = r["trace"]["walk"]
    assert (w["fallback"], w["fallback_reason"], w["steps_n"]) == (True, "no_walk_intent", 0)


async def test_start_from_vocab_when_no_candidates(deps_factory, hub):
    jev = FakeJev(intent="I-02", choices={"start": "family:M9", "nb": U140, "sec": "section:overview"})
    r, _, _ = await run(deps_factory, "그거 어떤 시술인가요", jev)
    w = r["trace"]["walk"]
    assert w["jev0"]["start_source"] == "vocab" and w["start_node"] == "family:M9" and not w["fallback"]


async def test_jev_error_is_column_error(deps_factory, hub):
    r, _, _ = await run(deps_factory, Q, FakeJev(status=500))
    assert r["error"].startswith("Jev 오류") and r["trace"]["walk"]["steps_n"] == 0


async def test_seed_reproducible(deps_factory, hub):
    ch = {"start": U140, "nb": U102, "sec": "section:aftercare"}
    orders = []
    for _ in range(2):
        jev = FakeJev(intent="I-03", choices=ch)
        await run(deps_factory, Q, jev, seed=7)
        orders.append([list(q["criteria"]) for req in jev.requests for q in req["questions"].values()
                       if q["type"] == "choice"])
    assert orders[0] == orders[1]
    jev = FakeJev(intent="I-03", choices=ch)
    await run(deps_factory, Q, jev, seed=8)
    assert [list(q["criteria"]) for req in jev.requests for q in req["questions"].values() if q["type"] == "choice"] \
        != orders[0]


async def test_two_stage_choice_over_255(deps_factory, hub):
    deps = deps_factory(anthropic=FakeAnthropic([Turn(text=["x"])]))
    jev = FakeJev(choices={"kind": "procedure_relation", "pick": "unit_procedure:U5-9007"})
    deps.jev = jev.client(deps.settings)
    opts = {f"section:s{i}": f"s{i}" for i in range(17)}
    opts |= {f"unit_procedure:U5-9{i:03d}": f"n{i}" for i in range(300)}
    kind_of = {k: ("section" if k.startswith("section:") else "procedure_relation") for k in opts}
    walk = {"jev_calls": 0, "jev_ms_total": 0.0}
    row = await case3.walk_pick(deps, timer(), 2, "q", {"node": {"name": "x", "kind": "unit_procedure"}}, opts, kind_of,
                                [], 0, {}, walk)
    assert row["two_stage"] is True and row["options_n"] == 317 and walk["jev_calls"] == 2
    assert row["kind"]["choice"] == "procedure_relation" and row["picked"] == ["unit_procedure:U5-9007"]
    assert len(jev.requests[1]["questions"]["pick"]["criteria"]) == 255  # 254 + 「없음(충분)」


async def test_rollback_switch_v1_only(deps_factory, hub):
    r, _, _ = await run(deps_factory, Q, FakeJev(intent="I-03"), walk_max_steps=0)
    assert "walk" not in r["trace"] and r["trace"]["hub"]["tool"] == "procedure_card"
    assert [t for t, _ in hub_calls(hub)] == ["resolve", "procedure_card"]


async def test_stages_cover_total(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": U140, "nb": U102, "sec": "section:aftercare"})
    r, _, _ = await run(deps_factory, Q, jev)
    st = r["timing"]["stages"]
    assert all(a["end_ms"] <= b["start_ms"] + 0.01 for a, b in pairwise(st))  # 차례대로 · 겹침 없음
    assert st[-1]["end_ms"] <= r["timing"]["total_ms"] + 0.01


def test_screen_walk_rows_and_marks():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        import pytest
        pytest.skip("node 없음")
    state = Path(__file__).resolve().parents[2] / "web" / "state.js"
    w = {"steps_n": 2, "stop": "budget", "bytes_total": 900, "budget_hit": True, "fact_ids": ["a", "b"], "jev_calls": 3,
         "fallback": False, "neighbor_reached": 1, "full": {"fact_ids": ["a"], "cut_unfetched_ids": ["b"]}, "steps": [
             {"k": 1, "node": U140, "phase": "first", "peek_bytes": 700, "jev": {"picked": ["section:aftercare"], "confidence": 0.8,
                                                                "enough": 0.1}, "expand": {"bytes": 900, "fact_ids": ["a", "b"]}},
             {"k": 2, "node": U102, "jev": {"picked": ["section:overview"]}, "expand": {"bytes": 50000, "excluded": True}}]}
    calls = [{"truncated": True, "intent_passed": False, "input": {"intent": "none"}},
             {"intent_passed": True, "input": {"intent": "I-02"}}, {"stray": True, "intent_passed": None},
             {"stray": True, "stray_kind": "bare_hub", "intent_passed": None}]
    script = ("const S=require(process.argv[1]);console.log(JSON.stringify([S.walkRows(" + json.dumps(w) + "),"
              + json.dumps(calls) + ".map(S.callMarks), S.stageKind('jev#2'), S.legendItems([{name:'hub_peek#1',start_ms:0,"
              "end_ms:2}])[0].label]))")
    rows, marks, kind, label = json.loads(subprocess.run([node, "-e", script, str(state)], capture_output=True, text=True,
                                                         check=True).stdout)
    assert rows["head"] == "걸음 2 · 멈춤 budget · 900 B (예산) · fact 2 · 이웃 fact 1 · 원문 1 / 한 줄 1 · Jev 3회"
    assert rows["steps"][0]["phase"] == "첫"
    assert rows["steps"][1]["excluded"] is True and rows["steps"][0]["facts"] == 2
    assert marks == [["✂ 잘림", "intent none ✕"], ["intent I-02"], ["격리 밖"], ["이름 틀림"]]
    assert kind == "jev" and label == "peek#1"


# ── 걷기 고침 2(11-01 · phub-recommend-walk-fix2.md) — 필수 묶음 · 「충분」 늦추기 · 의도별 고정 섹션 · 기록 ────────────
async def test_fix2_required_and_fixed_at_start(deps_factory, hub):
    """시술 S — Jev 고름 전에 checks · site_caution(필수) + I-03 고정 aftercare · recovery 를 받음 · Jev 섹션 선택지엔 안 뜸."""
    jev = FakeJev(intent="I-03", choices={"start": U140, "nb": U102, "sec": "section:overview"}, nouls={"enough": [0.9]})
    r, fa, _ = await run(deps_factory, Q, jev, fix2=True, walk_max_steps=1)
    w = r["trace"]["walk"]
    s1 = w["steps"][0]
    whys = [(c["sections"], c.get("why")) for c in s1["expand"]["calls"]]
    assert whys[:4] == [(["checks"], "required"), (["site_caution"], "required"), (["aftercare"], "fixed"),
                        (["recovery"], "fixed")]
    assert ([], None) in whys and (["overview"], None) in whys  # 그다음 이웃 slim · Jev 섹션
    crit = jev_qs(jev, 1)["sec"]["criteria"]
    assert not {"section:checks", "section:aftercare", "section:recovery"} & set(crit)
    assert w["fixed"] == {"node": U140, "step": 1, "want": ["aftercare", "recovery"], "sections": ["aftercare", "recovery"],
                          "filtered": []}
    req = w["required"]
    assert req["nodes"] == [U140] and [c["sections"] for c in req["calls"]] == [["checks"], ["site_caution"]]
    assert set(req["fact_ids"]) == {f"{U140}#checks#fixture[0]", f"{U140}#site_caution#fixture[0]"}
    assert req["bytes"] == sum(c["bytes"] for c in req["calls"]) and "calls_ref" not in req
    assert {f["id"] for f in sonnet_input(fa)["facts"]} >= set(req["fact_ids"])


async def test_fix2_required_end_for_neighbor_procedures(deps_factory, hub):
    """고민 S(시술 아님) — 걸음 뒤 모은 줄의 시술(rank 순 위 3)에 필수 묶음 · 고정 섹션은 첫 시술 노드(걸음 2)에서."""
    jev = FakeJev(intent="I-05", choices={"start": "concern:P3", "nb": U140, "pick": "section:overview"},
                  nouls={"enough": [0.1]})
    r, _, _ = await run(deps_factory, "처짐 시술 회복 기간", jev, fix2=True, walk_max_steps=2)
    w = r["trace"]["walk"]
    assert w["fixed"]["node"] == U140 and w["fixed"]["step"] == 2 and w["fixed"]["sections"] == ["recovery", "timeline"]
    assert w["required"]["nodes"] == [U140] and [c["sections"] for c in w["required"]["calls"]] == [["checks"],
                                                                                                    ["site_caution"]]
    names = [x["name"] for x in r["timing"]["stages"]]
    assert "hub_req#1" in names and "hub_req#1-2" in names and names.index("hub_req#1") < names.index("claude")
    assert w["neighbor_reached"] == 1


async def test_fix2_enough_ignored_under_half_budget(deps_factory, hub):
    """모은 바이트 < 예산 50% 면 「충분」(0.9 ≥ 0.8) 무시 · 넘으면 멈춤 · 기록 enough_ignored."""
    jev = FakeJev(intent="I-01", choices={"start": U140, "nb": U102, "sec": "section:overview", "pick": "section:method"},
                  nouls={"enough": [0.9, 0.9, 0.9]})
    r, _, _ = await run(deps_factory, Q, jev, fix2=True)
    w = r["trace"]["walk"]
    assert w["enough_ignored"] >= 1 and w["stop"] != "enough" and w["steps"][1]["jev"]["enough_ignored"] is True
    jev = FakeJev(intent="I-01", choices={"start": U140, "nb": U102, "sec": "section:overview", "pick": "section:method"},
                  nouls={"enough": [0.9]})
    r, _, _ = await run(deps_factory, Q, jev, fix2=True, walk_byte_budget=100)  # 예산 작음 → 50% 넘음 → 멈춤
    w = r["trace"]["walk"]
    assert w["enough_ignored"] == 0
    jev = FakeJev(intent="I-01", choices={"start": U140, "nb": U102, "sec": "section:overview", "pick": "section:method"},
                  nouls={"enough": [0.7]})
    r, _, _ = await run(deps_factory, Q, jev, fix2=True, walk_enough_bytes_ratio=0.0)
    assert r["trace"]["walk"]["steps"][1]["jev"]["enough"] == 0.7 and r["trace"]["walk"]["stop"] != "enough"  # 0.7 < 0.8


async def test_fix2_required_prefiltered_by_budget(deps_factory, hub):
    deps = deps_factory()
    p = await deps.hub_rest.peek(U140)
    by = {x["name"]: x["bytes"] for x in p["sections"]}
    jev = FakeJev(intent="I-01", choices={"start": U140, "nb": U102, "sec": "없음"}, nouls={"enough": [0.9]})
    r, _, _ = await run(deps_factory, Q, jev, fix2=True, walk_byte_budget=by["checks"] + 3, walk_max_steps=1)
    w = r["trace"]["walk"]
    assert [c["sections"] for c in w["required"]["calls"]] == [["checks"]] and w["required"]["filtered"] == [
        f"{U140}|site_caution"]


# ── 걷기 고침 3(11-01 · phub-recommend-walk-fix3.md) — Jev 는 이웃만 · 의도 묶음 바닥 · 남은 예산 채우기 ─────────────
I03 = ["aftercare", "recovery", "timeline", "emergency", "disclosure", "overview"]


async def test_fix3_base_bundle_and_neighbors_only(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": U140, "nb": U102, "pick": "없음(충분)"}, nouls={"enough": [0.1]})
    r, fa, _ = await run(deps_factory, Q, jev, profile="fix3", walk_max_steps=2, walk_fill=False)
    w = r["trace"]["walk"]
    s1, s2 = w["steps"]
    whys = [(c["sections"], c.get("why")) for c in s1["expand"]["calls"]]
    assert whys[:2] == [(["checks"], "required"), (["site_caution"], "required")]
    assert [x for x, why in whys if why == "bundle"] == [[x] for x in I03]  # §5-14 I-03 묶음 통째(묶음 순)
    assert w["base_bundle"] == {"node": U140, "step": 1, "want": I03, "sections": I03, "filtered": []}
    q1 = jev_qs(jev, 1)
    assert "sec" not in q1 and not any(k.startswith("sec_also:") for k in q1)  # Jev 는 이웃만
    assert all(not x.startswith("section:") for x in w["step1_options"])
    assert [b["node"] for b in w["bundles"]] == [U140, U102]  # 고른 이웃도 그 의도 묶음
    assert s2["expand"]["sections"] == I03
    assert all(not k.startswith("section:") for k in jev_qs(jev, 2)["pick"]["criteria"])
    assert w["fixed"]["sections"] == []  # I-03 고정(aftercare · recovery)은 묶음이 이미 받음
    assert {f["section"] for f in sonnet_input(fa)["facts"]} >= set(I03) | {"checks", "site_caution"}


async def test_fix3_base_bundle_at_first_procedure_node(deps_factory, hub):
    """고민 S — 묶음 바닥은 처음 걷는 시술 노드(걸음 2)."""
    jev = FakeJev(intent="I-05", choices={"start": "concern:P3", "nb": U140, "pick": "없음(충분)"}, nouls={"enough": [0.1]})
    r, _, _ = await run(deps_factory, "처짐 시술 회복 기간", jev, profile="fix3", walk_max_steps=2, walk_fill=False)
    w = r["trace"]["walk"]
    assert w["base_bundle"]["node"] == U140 and w["base_bundle"]["step"] == 2
    assert w["base_bundle"]["sections"] == ["recovery", "timeline", "aftercare", "overview", "disclosure", "ops"]


async def test_fix3_fill_until_budget(deps_factory, hub):
    jev = FakeJev(intent="I-02", choices={"start": U140, "nb": U102, "pick": "없음(충분)"}, nouls={"enough": [0.1]})
    r, _, _ = await run(deps_factory, Q, jev, profile="fix3", walk_max_steps=2)
    w = r["trace"]["walk"]
    fl = w["fill"]
    assert fl["sections"] and fl["bytes"] == sum(x["bytes"] for x in fl["sections"] if not x.get("excluded"))
    assert w["bytes_total"] <= 20000
    got = {(st["node"], x) for st in w["steps"] for x in (st.get("expand") or {}).get("sections") or []}
    assert not got & {(x["node"], x["section"]) for x in fl["sections"]}  # 이미 받은 섹션은 다시 안 받음
    first = fl["sections"][0]
    assert first["section"] not in ("overview", "method", "ops") or first["node"] == U102  # 묶음 순 → 그 밖
    stages = [x["name"] for x in r["timing"]["stages"]]
    assert "hub_fill#1" in stages and stages.index("hub_fill#1") < stages.index("claude")
    # 예산이 작으면 채우기가 예산에서 멈춤(넘는 섹션은 건너뜀)
    jev = FakeJev(intent="I-02", choices={"start": U140, "nb": U102, "pick": "없음(충분)"}, nouls={"enough": [0.1]})
    r2, _, _ = await run(deps_factory, Q, jev, profile="fix3", walk_max_steps=2, walk_byte_budget=w["bytes_total"] - 2000)
    w2 = r2["trace"]["walk"]
    assert w2["fill"]["skipped"] > 0 and w2["bytes_total"] <= w["bytes_total"] - 2000


async def test_fix3_enough_ignored_until_80pct(deps_factory, hub):
    jev = FakeJev(intent="I-01", choices={"start": U140, "nb": U102, "pick": "없음(충분)"}, nouls={"enough": [0.9, 0.9, 0.9]})
    r, _, _ = await run(deps_factory, Q, jev, profile="fix3", walk_fill=False)
    w = r["trace"]["walk"]
    assert w["bytes_total"] < 0.8 * 20000 and w["enough_ignored"] >= 1 and w["stop"] != "enough"


# ── 걷기 고침 4(11-01 · phub-recommend-walk-fix4.md) — 안전 예산 따로(8KB · 20KB 밖) ───────────────────────────────
SAFE = ["checks", "site_caution", "safety.side_effects", "safety.complications"]


async def test_fix4_safety_pool_first_and_outside_general(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": U140, "nb": U102, "pick": "없음(충분)"}, nouls={"enough": [0.1]})
    r, fa, _ = await run(deps_factory, Q, jev, profile="fix4", walk_max_steps=2, walk_fill=False)
    w = r["trace"]["walk"]
    calls = w["steps"][0]["expand"]["calls"]
    assert [c["sections"][0] for c in calls[:4]] == SAFE and all(c.get("pool") == "safety" for c in calls[:4])
    sb = w["safety_budget"]
    assert sb["budget"] == 8000 and sb["bytes"] == sum(x["bytes"] for x in sb["sections"]) <= 8000 and sb["cut"] == []
    assert [(x["node"], x["section"]) for x in sb["sections"][:4]] == [(U140, x) for x in SAFE]
    gen = sum(c["bytes"] for st in w["steps"] for c in (st.get("expand") or {}).get("calls") or []
              if not c.get("excluded") and c.get("pool") != "safety")
    assert w["bytes_total"] == gen  # 일반 예산(20KB)엔 안 셈
    # 걸음 뒤 시술 위 3(U102) 도 안전 섹션 — 안전 예산에서
    assert {(x["node"], x["section"]) for x in sb["sections"]} >= {(U102, x) for x in SAFE}
    assert {"safety.side_effects", "safety.complications"} <= {f["section"] for f in sonnet_input(fa)["facts"]}


async def test_fix4_safety_budget_cut_by_rank(deps_factory, hub):
    deps = deps_factory()
    p = await deps.hub_rest.peek(U140)
    by = {x["name"]: x["bytes"] for x in p["sections"]}
    budget = by["checks"] + by["site_caution"] + 3
    jev = FakeJev(intent="I-01", choices={"start": U140, "nb": U102, "pick": "없음(충분)"}, nouls={"enough": [0.1]})
    r, _, _ = await run(deps_factory, Q, jev, profile="fix4", walk_max_steps=1, walk_fill=False, walk_safety_budget=budget)
    sb = r["trace"]["walk"]["safety_budget"]
    assert [x["section"] for x in sb["sections"]] == ["checks", "site_caution"] and sb["bytes"] <= budget
    assert {(x["node"], x["section"]) for x in sb["cut"]} >= {(U140, "safety.side_effects"), (U140, "safety.complications")}


async def test_fix4_i04_management_in_safety_pool(deps_factory, hub):
    jev = FakeJev(intent="I-04", choices={"start": U140, "nb": U102, "pick": "없음(충분)"}, nouls={"enough": [0.1]})
    r, _, _ = await run(deps_factory, "소프웨이브 부작용", jev, profile="fix4", walk_max_steps=1, walk_fill=False)
    sb = r["trace"]["walk"]["safety_budget"]
    assert (U140, "safety.management") in {(x["node"], x["section"]) for x in sb["sections"]}


async def test_fix4_safety_at_first_procedure_node(deps_factory, hub):
    """고민 S · I-01(묶음 없음) — 처음 걷는 시술 노드에서 안전 섹션(안전 예산) → fact 0 되돌아감이 안 남."""
    jev = FakeJev(intent="I-01", choices={"start": "concern:P3", "nb": U140, "pick": "없음(충분)"}, nouls={"enough": [0.1]})
    r, _, _ = await run(deps_factory, "처짐이 고민이에요", jev, profile="fix4", walk_max_steps=2, walk_fill=False)
    w = r["trace"]["walk"]
    assert not w["fallback"] and [(x["node"], x["section"]) for x in w["safety_budget"]["sections"][:4]] == [
        (U140, x) for x in SAFE]
    assert w["required"]["nodes"][0] == U140
