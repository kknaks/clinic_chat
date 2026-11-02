"""Phase 4 — 측정 · 기록(가짜 시계 · 같은 t0 · 기록 줄 꼴 · 키 · env 값 없음)."""

from __future__ import annotations

import json

from clinic_chat import runner
from clinic_chat.cases import case2, case3
from clinic_chat.config import truncation_markers_sha
from clinic_chat.runs import RunStore
from clinic_chat.timing import CaseTimer, Clock
from tests.fake_anthropic import FakeAnthropic, FakeClock, Turn, answer_policy
from tests.fake_jev import FakeJev


async def test_stages_and_ttft_exact(deps_factory):
    clk = FakeClock()
    fa = FakeAnthropic([Turn(first_ms=100, tools=[("vocab", {})]),
                        Turn(first_ms=50, chunk_ms=10, text=["a", "b"])], clock=clk)
    c = Clock(now=clk.now)
    c.start()
    r = await case2.run(deps_factory(anthropic=fa), "q", CaseTimer(c), lambda t: None)
    st = [(s["name"], s["start_ms"], s["end_ms"]) for s in r["timing"]["stages"]]
    assert st == [("claude#1", 0.0, 100.0), ("tool:vocab#1", 100.0, 100.0), ("claude#2", 100.0, 170.0)]
    t = r["timing"]
    assert (t["ttft_ms"], t["ttft_answer_ms"], t["total_ms"], t["llm_round_trips"]) == (150.0, 150.0, 170.0, 2)
    assert r["tokens"]["claude#1"]["input_tokens"] == 100


async def test_ttft_excludes_thinking_and_first_text_any_round(deps_factory):
    clk = FakeClock()
    fa = FakeAnthropic([Turn(first_ms=10, text=["앞글 "], tools=[("vocab", {})]),
                        Turn(thinking="", first_ms=30, chunk_ms=20, text=["x"])], clock=clk)
    c = Clock(now=clk.now)
    c.start()
    r = await case2.run(deps_factory(anthropic=fa), "q", CaseTimer(c), lambda t: None)
    assert r["timing"]["ttft_ms"] == 10.0  # 어느 바퀴든 첫 text_delta
    assert r["timing"]["ttft_answer_ms"] == 60.0  # 마지막 바퀴 · thinking 델타(30) 제외


WALK = {"start": "unit_procedure:U5-140", "nb": "unit_procedure:U5-102", "sec": "section:overview"}  # 걸음 1: 이웃 + 섹션


async def test_case3_stage_times(deps_factory):
    clk = FakeClock()
    fa = FakeAnthropic([Turn(first_ms=40, chunk_ms=5, text=["a", "b"])], clock=clk)
    deps = deps_factory(anthropic=fa, walk_required_sections_csv="", walk_fixed_sections_by_intent="",
                        walk_jev_sections=True, walk_base_bundle=False, walk_fill=False,
                        walk_safety_budget=0)  # v2.4 단계 순서만
    deps.jev = FakeJev(intent="I-03", choices=WALK).client(deps.settings)
    deps.vocab = await deps.hub_rest.vocab()
    c = Clock(now=clk.now)
    c.start()
    r = await case3.run(deps, "소프웨이브 끝나고 사우나", CaseTimer(c), lambda t: None)
    names = [s["name"] for s in r["timing"]["stages"]]
    assert names[:6] == ["hub_resolve", "jev#0", "hub_peek#1", "jev#1", "hub_expand#1", "hub_expand#1-2"]
    assert names[-1] == "claude" and all(n.startswith(("hub_", "jev#")) for n in names[:-1])
    assert r["timing"]["ttft_ms"] == 40.0 and r["timing"]["total_ms"] == 50.0
    assert r["tokens"]["jev#0"]["input_tokens"] == 321 and r["tokens"]["claude"]["output_tokens"] == 20
    # v1 되돌림 스위치(WALK_MAX_STEPS=0) — 단계 이름은 v1 그대로
    deps0 = deps_factory(anthropic=FakeAnthropic([Turn(text=["a"])]), walk_max_steps=0)
    deps0.jev = FakeJev(intent="I-01").client(deps0.settings)
    deps0.vocab = deps.vocab
    r0 = await case3.run(deps0, "이마 주름", CaseTimer(Clock()), lambda t: None)
    assert [s["name"] for s in r0["timing"]["stages"]] == ["hub_resolve", "jev", "hub_path", "claude"]
    assert "walk" not in r0["trace"]


async def run_all(deps_factory, tmp_path, hub, monkeypatch, **kw):
    starts = []
    orig = Clock.start
    monkeypatch.setattr(Clock, "start", lambda self: starts.append(1) or orig(self))
    deps = deps_factory(anthropic=FakeAnthropic(answer_policy), **kw)
    deps.jev = FakeJev(intent="I-02").client(deps.settings)
    deps.vocab = await deps.hub_rest.vocab()
    deps.app_commit = "fixturecommit"
    events: list[tuple[str, dict]] = []
    rec = await runner.execute(deps, "소프웨이브 어떻게 해요?", lambda e, d: events.append((e, d)), quality=False,
                               jev_seed=3, judge_seed=4)
    return rec, events, starts, deps


async def test_three_cases_same_t0_and_record(deps_factory, tmp_path, hub, monkeypatch):
    rec, events, starts, deps = await run_all(deps_factory, tmp_path, hub, monkeypatch)
    assert len(starts) == 1  # t0 하나
    assert all(rec["cases"][c]["timing"]["t0"] == 0.0 for c in runner.CASES)
    assert all(rec["cases"][c]["error"] is None for c in runner.CASES), {c: rec["cases"][c]["error"] for c in runner.CASES}
    assert set(rec) == {"run_id", "asked_at", "question", "versions", "settings", "cases", "judge_order", "judge_seed",
                        "cost", "provider", "judge_mode"}
    assert rec["provider"] == "sdk" and rec["versions"]["answer_provider"] == "sdk" and rec["versions"]["auth_kind"] == "api"
    cost = rec["cost"]
    assert set(cost["cases"]) == set(runner.CASES) and cost["quality_usd"] is None
    assert cost["answers_usd"] == round(sum(cost["cases"].values()), 6) > 0 and cost["pricing_as_of"]
    v = rec["versions"]
    assert v["answer_model"] == "claude-sonnet-5-5" and v["jev_model"] == "jev-1.14.0"
    assert set(v["prompts"]) == {"common", "case1", "case2", "case3", "judge", "extract"}
    assert all(len(x) == 12 for x in v["prompts"].values())
    assert v["gold_build_hash"] == "fixture0build0h1" and v["raw_manifest_sha16"] == hub.store.dir_manifest_sha16
    assert v["app_commit"] == "fixturecommit"
    assert rec["settings"] == {"thinking": "between_tools", "effort": "high", "tool_loop_max": 12,
                               "max_budget_usd": None, "service_tier": None, "timeout_sec": None,
                               "input_cap_bytes": None, "mcp_result_max_chars": 200000,
                               "truncation_markers_sha": truncation_markers_sha(),
                               "walk_max_steps": 4, "walk_byte_budget": 20000, "walk_enough_min": 0.8,
                               "walk_sections_per_step": 2, "walk_neighbors_max": 3, "walk_first_section_max": 1,
                               "walk_first_section_max_by_intent": "I-02:2,I-03:2,I-04:2,I-05:2",
                               "walk_full_byte_budget": 15000, "walk_full_facts_max": 64,
                               "order_table_sha": case3.order_table(deps.settings)[1],
                               "walk_enough_bytes_ratio": 0.8, "walk_required_sections": "checks,site_caution",
                               "walk_required_max": 3,
                               "walk_fixed_sections_by_intent": deps.settings.walk_fixed_sections_by_intent,
                               "walk_safety_budget": 8000, "walk_safety_sections": "safety.side_effects,safety.complications",
                               "walk_safety_checks": True,
                               "walk_jev_sections": False, "walk_base_bundle": True, "walk_fill": True,
                               "jev_confidence_min": 0.3, "jev_option_order_seed": 3}
    for c in runner.CASES:
        assert set(rec["cases"][c]) >= {"answer", "stop_reason", "loop_capped", "error", "timing", "tokens", "trace",
                                        "word_check", "judge"}
        assert set(rec["cases"][c]["timing"]) >= {"ttft_ms", "ttft_answer_ms", "total_ms", "stages", "llm_round_trips"}
    kinds = [e for e, _ in events]
    assert kinds.count("case_done") == 3 and "text" in kinds and "stage" in kinds

    store = RunStore(deps.settings.runs_path)
    store.append_run(rec)
    line = (deps.settings.runs_path / "runs.jsonl").read_text(encoding="utf-8")
    assert json.loads(line)["run_id"] == rec["run_id"]
    assert "fixture-key" not in line  # 키 없음
    for val in (deps.settings.raw_dir, deps.settings.semantic_json, str(deps.settings.runs_path), str(tmp_path)):
        assert val not in line  # env 경로 값 없음
    row = store.add_human_check(rec["run_id"], "case2", "맞음", note="fixture")
    assert store.checks() == [row]
    lst = store.list()
    assert lst[0]["cases"]["case2"]["human"] == "맞음" and lst[0]["gold"] == "fixture0"


async def test_one_column_error_others_continue(deps_factory, tmp_path, hub, monkeypatch):
    hub.fail_tools.add("resolve")  # case3 ① 와 case2 의 resolve 둘 다 고장
    rec, events, _, _ = await run_all(deps_factory, tmp_path, hub, monkeypatch)
    assert rec["cases"]["case3"]["error"]
    assert rec["cases"]["case1"]["error"] is None and rec["cases"]["case1"]["answer"]
    assert rec["cases"]["case2"]["error"] is None and rec["cases"]["case2"]["trace"]["tool_calls"][0]["is_error"]
    assert ("error", {"case": "case3", "message": rec["cases"]["case3"]["error"]}) in events


def test_pricing_table():
    from clinic_chat.pricing import cost_usd

    u = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_creation_input_tokens": 200_000,
         "cache_read_input_tokens": 500_000}
    # Sonnet 5.5: 2 + 1.0 + 0.5 + 0.1
    assert cost_usd("claude-sonnet-5-5", u) == 3.6
    # Opus 5.5: 4 + 2.0 + 1.0 + 0.1
    assert cost_usd("claude-opus-5-5", u) == 7.1
    assert cost_usd("jev-1.14.0", {"input_tokens": 1_000_000}) == 0.042
    assert cost_usd("unknown-model", u) is None


async def test_case3_cost_and_system_cache_only(deps_factory):
    fa = FakeAnthropic([Turn(text=["답"])])
    deps = deps_factory(anthropic=fa)
    deps.jev = FakeJev(intent="I-03", choices=WALK).client(deps.settings)
    deps.vocab = await deps.hub_rest.vocab()
    c = Clock()
    c.start()
    r = await case3.run(deps, "소프웨이브 끝나고 사우나", CaseTimer(c), lambda t: None)
    p = fa.calls[0]
    assert p["system"][-1]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in p  # 1회 호출 — 질문 뒤 표시 안 함
    assert {"jev#0", "jev#1", "claude"} <= set(r["cost"]["by_stage"]) and r["cost"]["usd"] > 0
    assert all(k == "claude" or k.startswith("jev#") for k in r["cost"]["by_stage"])  # 허브 단계는 비용 없음


async def test_gold_hash_falls_back_to_health(deps_factory, tmp_path, hub, monkeypatch):
    deps = deps_factory(anthropic=FakeAnthropic(answer_policy))
    deps.jev = None  # case3 키 없음 → 허브 응답 머리 없음
    deps.startup["gold_build_hash"] = "d3a1abd52f70afe9"
    rec = await runner.execute(deps, "q", lambda e, d: None, quality=False, provider="sdk")
    assert rec["versions"]["gold_build_hash"] in ("fixture0build0h1", "d3a1abd52f70afe9")
    deps2 = deps_factory(anthropic=None)
    deps2.startup["gold_build_hash"] = "d3a1abd52f70afe9"
    rec2 = await runner.execute(deps2, "q", lambda e, d: None, quality=False, provider="sdk")
    assert rec2["versions"]["gold_build_hash"] == "d3a1abd52f70afe9"


async def test_cases_option_runs_case3_only(deps_factory, hub):
    """배치 `--cases case3` — 안 돌린 열은 skipped 빈 열(호출 · 채점 없음)."""
    deps = deps_factory(anthropic=FakeAnthropic(answer_policy))
    deps.jev = FakeJev(intent="I-02").client(deps.settings)
    deps.vocab = await deps.hub_rest.vocab()
    hub.calls.clear()
    rec = await runner.execute(deps, "소프웨이브 어떻게 해요?", lambda e, d: None, quality=False, cases=("case3",))
    assert rec["cases_run"] == ["case3"]
    assert rec["cases"]["case1"]["skipped"] is True and rec["cases"]["case2"]["skipped"] is True
    assert "skipped" not in rec["cases"]["case3"] and rec["cases"]["case3"]["error"] is None
    assert not any(via in ("gold-mcp", "raw-mcp") for via, _, _ in hub.calls)  # case1 · 2 도구 호출 0
    assert runner.gradable(rec["cases"]) == ["case3"]
