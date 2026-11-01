"""case3 Jev 걸음(SPEC-006 v2 §3-4) — 모의 Jev · 모의 허브(peek · expand · SPEC-005 §5-16 꼴) · 모의 Anthropic.

멈춤 4종 · 예산 넘는 expand 는 통째로 뺌 · 되돌아가기 3 사유 · 2단 Choice · seed 재현 · walk 칸 · 단계 · Jev 오류 ·
걸음 판은 intent 를 넘기되 의도 묶음 · 동봉 도구(T2~T7)는 안 부름.
"""

from __future__ import annotations

import json
from itertools import pairwise

from clinic_chat.cases import case3
from clinic_chat.timing import CaseTimer, Clock
from tests.fake_anthropic import FakeAnthropic, Turn
from tests.fake_jev import FakeJev

U140, U102 = "unit_procedure:U5-140", "unit_procedure:U5-102"
WALK_KEYS = {"start_node", "intent", "steps", "stop", "steps_n", "bytes_total", "budget_hit", "fallback", "fallback_reason",
             "jev_calls", "jev_ms_total", "hub_calls", "hub_ms_total", "fact_ids"}


def timer() -> CaseTimer:
    c = Clock()
    c.start()
    return CaseTimer(c)


def also(*names):
    """짝 Noul — instructions 에 이름이 든 선택지만 0.9(나머지 0.1) · 「충분」 은 기본(0.1)."""
    def fn(name, instr):
        if name.startswith("also:"):
            return 0.9 if any(n in instr for n in names) else 0.1
        return None
    return fn


async def run(deps_factory, q, jev: FakeJev, seed=0, **kw):
    fa = FakeAnthropic([Turn(text=["답변"])])
    deps = deps_factory(anthropic=fa, **kw)
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


async def test_two_steps_then_enough(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": U140, "pick": ["section:aftercare", "section:overview"]},
                  nouls={"enough": [0.1, 0.9]}, noul_fn=also("울쎄라"))
    r, fa, _ = await run(deps_factory, Q, jev)
    assert r["error"] is None, r["error"]
    w = r["trace"]["walk"]
    assert set(w) >= WALK_KEYS
    assert (w["start_node"], w["intent"], w["stop"], w["steps_n"], w["fallback"]) == (U140, "I-03", "enough", 2, False)
    s1, s2 = w["steps"]
    assert s1["node"] == U140 and s1["jev"]["picked"] == ["section:aftercare", U102]
    assert s1["expand"]["sections"] == ["aftercare"] and s1["expand"]["neighbors"] == [U102]
    assert s1["expand"]["fact_ids"] == [f"{U140}#aftercare#fixture[{k}]" for k in range(3)]
    assert s2["node"] == U102 and "expand" not in s2 and s2["jev"]["enough"] == 0.9  # 충분 → 펼치지 않고 멈춤
    assert w["bytes_total"] == s1["expand"]["bytes"] and w["fact_ids"] == s1["expand"]["fact_ids"]
    assert w["jev_calls"] == 3 and w["hub_calls"] == 3 and s1["peek_bytes"] > 0
    for k in ("confidence", "p_max", "ratio", "ms", "usage", "options_n"):
        assert k in s1["jev"]
    assert "hub" not in r["trace"]  # 걸음 판은 trace.hub 없음(되돌아간 판에만)
    names = [s["name"] for s in r["timing"]["stages"]]
    assert names == ["hub_resolve", "jev#0", "hub_peek#1", "jev#1", "hub_expand#1", "hub_peek#2", "jev#2", "claude"]
    # 걸음 호출 = peek · expand 만 · intent 를 넘김(허브는 받기만) · 의도 묶음 도구(T2~T7) 안 부름
    calls = hub_calls(hub)
    assert [t for t, _ in calls] == ["resolve", "peek", "expand", "peek"]
    assert all(a.get("intent") == "I-03" for t, a in calls if t in ("peek", "expand"))
    p = sonnet_input(fa)
    assert [st["step"] for st in p["steps"]] == [1]
    assert p["claude_contract"]["allowed_fact_ids"] == w["fact_ids"]
    assert set(p["safety"]) == {U140, U102}  # 노드마다 한 번(peek · expand 이웃)
    assert p["steps"][0]["facts"][0] == {"id": f"{U140}#aftercare#fixture[0]", "text": "fixture 소프웨이브 aftercare 0",
                                         "section": "aftercare"}


async def test_stop_steps(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": U140, "pick": "section:aftercare"}, noul_fn=also("울쎄라"))
    r, _, _ = await run(deps_factory, Q, jev, walk_max_steps=1)
    w = r["trace"]["walk"]
    assert (w["stop"], w["steps_n"], w["fallback"]) == ("steps", 1, False) and len(w["fact_ids"]) == 3


async def test_stop_empty_after_facts(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": U140, "pick": "section:aftercare"})
    r, _, _ = await run(deps_factory, Q, jev)
    w = r["trace"]["walk"]
    assert (w["stop"], w["steps_n"], w["fallback"]) == ("empty", 1, False)  # 이웃을 안 골라 다음 노드 없음


async def test_budget_excludes_whole_expand(deps_factory, hub):
    picks = {"start": U140, "pick": ["section:aftercare", "section:overview"]}
    r0, _, _ = await run(deps_factory, Q, FakeJev(intent="I-03", choices=picks, noul_fn=also("울쎄라")))
    b1 = r0["trace"]["walk"]["steps"][0]["expand"]["bytes"]
    hub.calls.clear()
    r, fa, _ = await run(deps_factory, Q, FakeJev(intent="I-03", choices=picks, noul_fn=also("울쎄라")),
                         walk_byte_budget=b1 + 1)
    w = r["trace"]["walk"]
    assert (w["stop"], w["budget_hit"], w["fallback"], w["bytes_total"]) == ("budget", True, False, b1)
    s2 = w["steps"][1]
    assert s2["expand"]["excluded"] == ["overview"] and s2["expand"]["calls"][0]["bytes"] > 1  # 불렀지만 통째로 뺌
    assert s2["expand"]["bytes"] == 0 and s2["expand"]["fact_ids"] == []
    assert w["fact_ids"] == w["steps"][0]["expand"]["fact_ids"]
    assert all(f["section"] == "aftercare" for st in sonnet_input(fa)["steps"] for f in st["facts"])


async def test_fallback_no_start(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": "해당 없음"})
    r, fa, _ = await run(deps_factory, Q, jev)
    w = r["trace"]["walk"]
    assert (w["fallback"], w["fallback_reason"], w["steps_n"]) == (True, "no_start", 0)
    assert r["trace"]["hub"]["tool"] == "procedure_card" and r["trace"]["hub"]["path"] == "I-03"
    assert [s["name"] for s in r["timing"]["stages"]] == ["hub_resolve", "jev#0", "jev", "hub_path", "claude"]
    assert "[허브 결과]" in fa.calls[0]["messages"][0]["content"]  # v1 꼴 입력
    assert r["error"] is None


async def test_fallback_peek_failed(deps_factory, hub):
    hub.fail_tools.add("peek")
    r, _, _ = await run(deps_factory, Q, FakeJev(intent="I-03", choices={"start": U140}))
    w = r["trace"]["walk"]
    assert (w["fallback"], w["fallback_reason"]) == (True, "peek_failed") and "error" in w["steps"][0]
    assert r["trace"]["hub"]["tool"] and r["error"] is None


async def test_fallback_no_facts(deps_factory, hub):
    # 고민 노드 = 섹션 없음 · 이웃만 → 「없음(충분)」 → 모은 fact 0
    jev = FakeJev(intent="I-01", choices={"start": "concern:P3", "pick": "없음(충분)"})
    r, _, _ = await run(deps_factory, "처짐이 고민이에요", jev)
    w = r["trace"]["walk"]
    assert (w["stop"], w["fallback"], w["fallback_reason"]) == ("enough", True, "no_facts")
    assert r["trace"]["hub"]["tool"] == "recommend"


async def test_start_from_vocab_when_no_candidates(deps_factory, hub):
    jev = FakeJev(intent="I-02", choices={"start": "family:M9", "pick": "section:overview"})
    r, _, _ = await run(deps_factory, "그거 어떤 시술인가요", jev)
    w = r["trace"]["walk"]
    assert w["jev0"]["start_source"] == "vocab" and w["start_node"] == "family:M9" and not w["fallback"]


async def test_jev_error_is_column_error(deps_factory, hub):
    r, _, _ = await run(deps_factory, Q, FakeJev(status=500))
    assert r["error"].startswith("Jev 오류") and r["trace"]["walk"]["steps_n"] == 0


async def test_seed_reproducible(deps_factory, hub):
    orders = []
    for _ in range(2):
        jev = FakeJev(intent="I-03", choices={"start": U140, "pick": "section:aftercare"})
        await run(deps_factory, Q, jev, seed=7)
        orders.append([list(q["criteria"]) for req in jev.requests for q in req["questions"].values()
                       if q["type"] == "choice"])
    assert orders[0] == orders[1]
    jev = FakeJev(intent="I-03", choices={"start": U140, "pick": "section:aftercare"})
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
    row = await case3.walk_pick(deps, timer(), 1, "q", {"node": {"name": "x", "kind": "unit_procedure"}}, opts, kind_of,
                                [], 0, {}, walk)
    assert row["two_stage"] is True and row["options_n"] == 317 and walk["jev_calls"] == 2
    assert row["kind"]["choice"] == "procedure_relation" and row["picked"] == ["unit_procedure:U5-9007"]
    assert len(jev.requests[1]["questions"]["pick"]["criteria"]) == 255  # 254 + 「없음(충분)」


async def test_rollback_switch_v1_only(deps_factory, hub):
    r, _, _ = await run(deps_factory, Q, FakeJev(intent="I-03"), walk_max_steps=0)
    assert "walk" not in r["trace"] and r["trace"]["hub"]["tool"] == "procedure_card"
    assert [t for t, _ in hub_calls(hub)] == ["resolve", "procedure_card"]


async def test_stages_cover_total(deps_factory, hub):
    jev = FakeJev(intent="I-03", choices={"start": U140, "pick": "section:aftercare"})
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
         "fallback": False, "steps": [
             {"k": 1, "node": U140, "peek_bytes": 700, "jev": {"picked": ["section:aftercare"], "confidence": 0.8,
                                                                "enough": 0.1}, "expand": {"bytes": 900, "fact_ids": ["a", "b"]}},
             {"k": 2, "node": U102, "jev": {"picked": ["section:overview"]}, "expand": {"bytes": 50000, "excluded": True}}]}
    calls = [{"truncated": True, "intent_passed": False, "input": {"intent": "none"}},
             {"intent_passed": True, "input": {"intent": "I-02"}}, {"stray": True, "intent_passed": None}]
    script = ("const S=require(process.argv[1]);console.log(JSON.stringify([S.walkRows(" + json.dumps(w) + "),"
              + json.dumps(calls) + ".map(S.callMarks), S.stageKind('jev#2'), S.legendItems([{name:'hub_peek#1',start_ms:0,"
              "end_ms:2}])[0].label]))")
    rows, marks, kind, label = json.loads(subprocess.run([node, "-e", script, str(state)], capture_output=True, text=True,
                                                         check=True).stdout)
    assert rows["head"].startswith("걸음 2 · 멈춤 budget · 900 B (예산) · fact 2 · Jev 3회")
    assert rows["steps"][1]["excluded"] is True and rows["steps"][0]["facts"] == 2
    assert marks == [["✂ 잘림", "intent none ✕"], ["intent I-02"], ["격리 밖"]]
    assert kind == "jev" and label == "peek#1"


async def test_sections_expand_one_by_one_until_budget(deps_factory, hub):
    """섹션은 하나씩 expand(Choice 1위 → 짝 Noul 높은 순) · 이웃 slim 은 첫 호출에 · 처음 넘는 섹션은 빼고 budget 멈춤."""
    jev = FakeJev(intent="I-03", choices={"start": U140, "pick": "section:aftercare"}, noul_fn=also("method", "울쎄라"))
    r0, _, _ = await run(deps_factory, Q, jev)
    calls = r0["trace"]["walk"]["steps"][0]["expand"]["calls"]
    assert [c["sections"] for c in calls] == [["aftercare"], ["method"]] and calls[0]["neighbors"] == [U102]
    assert calls[1]["neighbors"] == []
    b1 = calls[0]["bytes"]
    hub.calls.clear()
    jev = FakeJev(intent="I-03", choices={"start": U140, "pick": "section:aftercare"}, noul_fn=also("method", "울쎄라"))
    r, fa, _ = await run(deps_factory, Q, jev, walk_byte_budget=b1 + 1)
    w = r["trace"]["walk"]
    ex = w["steps"][0]["expand"]
    assert (w["stop"], w["budget_hit"], w["fallback"], w["steps_n"]) == ("budget", True, False, 1)
    assert ex["sections"] == ["aftercare"] and ex["excluded"] == ["method"] and ex["bytes"] == b1 == w["bytes_total"]
    assert [c.get("excluded", False) for c in ex["calls"]] == [False, True]
    assert {f["section"] for st in sonnet_input(fa)["steps"] for f in st["facts"]} == {"aftercare"}
    stages = [s["name"] for s in r["timing"]["stages"]]
    assert "hub_expand#1" in stages and "hub_expand#1-2" in stages
