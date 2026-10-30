"""Phase 3 — case3(모의 Jev + 모의 허브 REST + 모의 Anthropic)."""

from __future__ import annotations

import json

import pytest

from clinic_chat.cases import case3
from clinic_chat.timing import CaseTimer, Clock
from tests.fake_anthropic import FakeAnthropic, Turn
from tests.fake_hub import gold
from tests.fake_jev import FakeJev


def timer() -> CaseTimer:
    c = Clock()
    c.start()
    return CaseTimer(c)


async def run(deps_factory, q, jev: FakeJev, seed=0, **kw):
    fa = FakeAnthropic([Turn(text=["답변"])])
    deps = deps_factory(anthropic=fa, **kw)
    deps.jev = jev.client(deps.settings)
    deps.vocab = await deps.hub_rest.vocab()
    r = await case3.run(deps, q, timer(), lambda t: None, seed=seed)
    return r, fa, deps


def gold_calls(hub):
    return [(t, a) for via, t, a in hub.calls if via == "gold-rest" and t not in ("vocab",)]


# 의도마다 경로 1개(SPEC-005 §6-1)
CASES = [
    ("I-01", "이마 주름 신경 쓰여요", "recommend", {"concerns": ["concern:P2"], "site": "site:이마"}),
    ("I-02", "소프웨이브 어떻게 하는 거예요?", "procedure_card",
     {"id": "unit_procedure:U5-140", "sections": ["method"]}),
    ("I-03", "소프웨이브 끝나고 사우나 가도 돼요?", "procedure_card",
     {"id": "unit_procedure:U5-140", "sections": ["aftercare"]}),
    ("I-04", "소프웨이브 부작용 있어요?", "procedure_card",
     {"id": "unit_procedure:U5-140", "sections": ["safety.side_effects", "safety.complications"]}),
    ("I-05", "소프웨이브 하고 바로 출근 가능해요?", "procedure_card",
     {"id": "unit_procedure:U5-140", "sections": ["recovery"]}),
    ("I-06", "울쎄라 소프웨이브 뭐가 달라요?", "compare",
     {"procedures": ["unit_procedure:U5-102", "unit_procedure:U5-140"]}),
    ("I-07", "소프웨이브 다시 하려면 얼마나 있다가?", "pair_check",
     {"procedures": ["unit_procedure:U5-140", "unit_procedure:U5-140"]}),
    ("I-08", "6주 전에 울쎄라 했는데 소프웨이브 괜찮아요?", "pair_check",
     {"procedures": ["unit_procedure:U5-102", "unit_procedure:U5-140"],
      "prior": {"procedure": "unit_procedure:U5-102", "elapsed_days": 42}}),
    ("I-09", "임신 준비 중인데 소프웨이브 해도 돼요?", "check_conditions",
     {"id": "unit_procedure:U5-140", "conditions": ["check_condition:C-PREG"]}),
    ("I-10", "소프웨이브 몇 번 해요?", "procedure_card", {"id": "unit_procedure:U5-140", "sections": ["dose"]}),
    ("I-11", "처짐/탄력 패키지 뭐 있어요?", "combos", {"menu": "처짐/탄력"}),
]


@pytest.mark.parametrize("intent,q,tool,inp", CASES, ids=[c[0] for c in CASES])
async def test_intent_path(deps_factory, hub, intent, q, tool, inp):
    r, fa, _ = await run(deps_factory, q, FakeJev(intent=intent))
    assert r["error"] is None, r["error"]
    calls = gold_calls(hub)
    assert [t for t, _ in calls] == ["resolve", tool]  # ① 1번 + ③ 1번
    assert r["trace"]["hub"]["tool"] == tool and r["trace"]["hub"]["input"] == inp
    assert r["trace"]["hub"]["node_ids"]
    assert [s["name"] for s in r["timing"]["stages"]] == ["hub_resolve", "jev", "hub_path", "claude"]
    assert r["trace"]["hub_list"] == "cache" and r["trace"]["jev_calls"] == 1
    p = fa.calls[0]
    assert "tools" not in p and p["thinking"] == {"type": "between_tools"} and p["output_config"] == {"effort": "high"}
    assert r["answer"] == "답변"


async def test_i09_without_condition_falls_back_to_card(deps_factory, hub):
    r, _, _ = await run(deps_factory, "소프웨이브 통증 민감한데 괜찮아요?", FakeJev(intent="I-09"))
    assert r["trace"]["hub"]["tool"] == "procedure_card"
    assert r["trace"]["hub"]["input"]["sections"] == ["ops"]  # Jev 가 고른 칸(통증 → ops)


async def test_i09_without_condition_and_no_info_uses_spec_default(deps_factory, hub):
    r, _, _ = await run(deps_factory, "소프웨이브 저한테 괜찮을까요", FakeJev(intent="I-09"))
    assert r["trace"]["hub"]["input"]["sections"] == ["overview", "ops"]  # SPEC-005 §6-1 기본


# 「필요한 정보」 — Jev 가 고른 칸만 허브에(브리프 질문 꼴 예시)
INFO_CASES = [
    ("I-01", "이마 주름이 신경 쓰여요", "recommend", None),  # 추천 → 기본만
    ("I-09", "임신 중인데 소프웨이브 해도 돼요?", "check_conditions", ["checks"]),
    ("I-04", "소프웨이브 부작용 있어요?", "procedure_card", ["safety.side_effects", "safety.complications"]),
    ("I-05", "소프웨이브 회복 기간이랑 관리는 어떻게 해요?", "procedure_card", ["method", "recovery", "aftercare"]),
]


@pytest.mark.parametrize("intent,q,tool,secs", INFO_CASES, ids=[c[0] + "-" + c[1][:6] for c in INFO_CASES])
async def test_jev_picks_needed_sections(deps_factory, hub, intent, q, tool, secs):
    jev = FakeJev(intent=intent)
    fa = FakeAnthropic([Turn(text=["답"])])
    deps = deps_factory(anthropic=fa)
    deps.jev = jev.client(deps.settings)
    deps.vocab = await deps.hub_rest.vocab()
    deps.section_tools = {"procedure_card", "recommend", "compare"}
    r = await case3.run(deps, q, CaseTimer(Clock()), lambda t: None)
    asked = jev.requests[0]["questions"]
    assert {k for k in asked if k.startswith("info:")} == {f"info:{s}" for s in case3.INFO_SECTIONS}  # 같은 한 요청
    assert len(jev.requests) == 1
    j = r["trace"]["jev"]
    assert j["sections"] == (secs or [])
    if intent == "I-09":
        assert "checks" in j["sections"] and r["trace"]["hub"]["tool"] == tool
        return
    assert r["trace"]["hub"]["tool"] == tool
    sent = next(a for via, t, a in hub.calls if via == "gold-rest" and t == tool)
    assert sent.get("sections") == secs  # 기본만이면 sections 없음


async def test_i12_price_no_hub_call(deps_factory, hub):
    r, fa, _ = await run(deps_factory, "소프웨이브 얼마예요?", FakeJev(intent="I-12"))
    assert [t for t, _ in gold_calls(hub)] == ["resolve"]  # ③ 허브 호출 0
    assert r["trace"]["hub"]["tool"] is None
    assert '"price_unavailable": true' in fa.calls[0]["messages"][0]["content"]


async def test_missing_slot_asks_back(deps_factory, hub):
    r, fa, _ = await run(deps_factory, "그거 어떻게 하는 거예요?", FakeJev(intent="I-02"))
    assert [t for t, _ in gold_calls(hub)] == ["resolve"]
    assert r["trace"]["hub"]["missing"] == ["procedure"]
    assert "needs_clarification" in fa.calls[0]["messages"][0]["content"]


async def test_over_255_slot_reduced(deps_factory, hub, monkeypatch):
    many = {f"product:FX-{i}": {"name": f"fixture 제품 {i}", "kind": "product"} for i in range(400)}
    monkeypatch.setattr(gold, "NODES", {**gold.NODES, **many})
    jev = FakeJev(intent="I-02")
    fa = FakeAnthropic([Turn(text=["답"])])
    deps = deps_factory(anthropic=fa)
    deps.jev = jev.client(deps.settings)
    v = await deps.hub_rest.vocab()
    v["vocab"]["family"] = [{"id": f"family:FX{i}", "name": f"계열 {i}"} for i in range(300)] + v["vocab"]["family"]
    deps.vocab = v
    r = await case3.run(deps, "소프웨이브 어떻게 해요?", timer(), lambda t: None)
    sent = jev.requests[0]["questions"]
    assert all(len(q["criteria"]) <= 255 for q in sent.values() if q["type"] == "choice")
    assert r["trace"]["jev"]["slots"]["family"]["reduced"] is True
    assert r["trace"]["jev"]["slots"]["procedure"]["options_n"] == 1  # ① 후보(소프웨이브)만


async def test_option_order_seed_reproducible(deps_factory, hub):
    orders = []
    for seed in (7, 7, 8):
        jev = FakeJev(intent="I-01")
        await run(deps_factory, "이마 주름", jev, seed=seed)
        orders.append({k: list(q.get("criteria") or []) for k, q in jev.requests[0]["questions"].items()})
    assert orders[0] == orders[1] and orders[0] != orders[2]


async def test_low_confidence_marked(deps_factory, hub):
    r, fa, _ = await run(deps_factory, "이마 주름", FakeJev(intent="I-01", flat={"intent", "slot:concern"}))
    j = r["trace"]["jev"]
    assert j["intent"]["low_confidence"] is True and j["intent"]["confidence"] < 0.3
    assert j["slots"]["concern"]["low_confidence"] is True
    assert j["intent"]["p_max"] > 0 and j["intent"]["ratio"] is not None
    payload = fa.calls[0]["messages"][0]["content"].split("\n", 3)[-1]
    assert json.loads(payload)["slots"]["concern"]["low_confidence"] is True


async def test_multi_concern_noul(deps_factory, hub):
    r, _, _ = await run(deps_factory, "주름 처짐 같이 고민이에요", FakeJev(intent="I-01"))
    assert set(r["trace"]["hub"]["input"]["concerns"]) == {"concern:P2", "concern:P3"}
    assert r["trace"]["jev"]["noul"]["concern:P3"] >= 0.5


async def test_jev_request_shape_and_record(deps_factory, hub):
    jev = FakeJev(intent="I-01")
    r, _, _ = await run(deps_factory, "이마 주름", jev)
    body = jev.requests[0]
    assert body["model"] == "jev-1.14.0" and body["state"] == "이마 주름"
    q = body["questions"]
    assert q["intent"]["type"] == "choice" and len(q["intent"]["criteria"]) == 12
    assert "해당 없음" in q["slot:concern"]["criteria"] and q["said:concern"]["type"] == "noul"
    assert all(" · 예: " in d for d in q["intent"]["criteria"].values())
    j = r["trace"]["jev"]
    assert j["model"] == "jev-1.14.0" and j["usage"]["input_tokens"] == 321 and j["option_order_seed"] == 0
    assert r["tokens"]["jev"] == {"input_tokens": 321}


@pytest.mark.parametrize("jev,msg", [(FakeJev(status=401), "401"), (FakeJev(status=422), "422"),
                                     (FakeJev(timeout=True), "Timeout")])
async def test_jev_failure_only_this_column(deps_factory, hub, jev, msg):
    r, fa, _ = await run(deps_factory, "이마 주름", jev)
    assert r["error"] and msg in r["error"]
    assert fa.calls == []  # Sonnet 안 부름
    assert [t for t, _ in gold_calls(hub)] == ["resolve"]


async def test_no_key(deps_factory):
    deps = deps_factory(anthropic=FakeAnthropic([Turn(text=["x"])]))
    deps.jev = None
    r = await case3.run(deps, "q", timer(), lambda t: None)
    assert r["error"] == "키 없음"


async def test_jev_slot_used_when_resolve_misses(deps_factory, hub):
    """run 926bc4477b02 꼴 — resolve 가 「주름이」(조사)를 못 찾아 T1 고민 후보 없음 + Jev concern 0.97 → recommend 호출."""
    jev = FakeJev(intent="I-01", choices={"slot:concern": "concern:P2"})
    r, fa, _ = await run(deps_factory, "이마 주름이 신경 쓰이는데 어떤 시술이 좋아요?", jev)
    assert "concern:P2" not in r["trace"]["hub"]["resolve"]["candidates"]  # T1 에 고민 후보 없음
    assert r["trace"]["jev"]["slots"]["concern"]["confidence"] >= 0.3
    assert r["trace"]["hub"]["tool"] == "recommend"
    assert r["trace"]["hub"]["input"] == {"concerns": ["concern:P2"], "site": "site:이마"}
    assert [t for t, _ in gold_calls(hub)] == ["resolve", "recommend"]
    assert "needs_clarification" not in fa.calls[0]["messages"][0]["content"]


async def test_low_confidence_slot_asks_back(deps_factory, hub):
    jev = FakeJev(intent="I-01", choices={"slot:concern": "concern:P2"}, flat={"slot:concern"})
    r, _fa, _ = await run(deps_factory, "이마 주름이 신경 쓰이는데 어떤 시술이 좋아요?", jev)
    assert r["trace"]["jev"]["slots"]["concern"]["low_confidence"] is True
    assert r["trace"]["hub"]["tool"] is None and r["trace"]["hub"]["missing"] == ["concern"]
    assert [t for t, _ in gold_calls(hub)] == ["resolve"]
