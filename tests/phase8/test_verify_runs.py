"""기록 검증(SPEC-006 v2 §4-4) — 모의 허브 재호출로 잘림 · intent · stray · 걸음 · 공정성 · C 를 판단 없이 센다 · 두 번 같음."""

from __future__ import annotations

import json

import pytest

from clinic_chat import verify_runs as V
from clinic_chat.clients.hub_mcp import HubMCP, asgi_client_factory
from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.config import ROOT
from tests.conftest import make_settings
from tests.fake_hub import RAW_MCP_URL, REST_URL
from tests.fake_hub.gold import BUILD

BATCH = "bfixture-verify"
CARD = {"id": "unit_procedure:U5-140", "sections": ["aftercare"], "intent": "I-03"}
CHK = {"id": "unit_procedure:U5-140", "intent": "none"}


def tc(tool, inp, delivered, **kw):
    return {"tool": tool, "input": inp, "output_bytes": delivered, "delivered_bytes": delivered, "is_error": False, **kw}


def rec(rid, no, gold, case1=(), case2=(), case3=None, provider="claude-cli"):
    return {"run_id": rid, "question_no": no, "batch_id": BATCH, "provider": provider,
            "versions": {"gold_build_hash": gold},
            "cases": {"case1": {"trace": {"tool_calls": list(case1)}}, "case2": {"trace": {"tool_calls": list(case2)}},
                      "case3": {"trace": case3 or {}}}}


@pytest.fixture
async def setup(tmp_path, hub):
    f = asgi_client_factory(hub.app)
    rest, raw = HubRest(REST_URL, f), HubMCP(RAW_MCP_URL, f)
    _, card_txt = await rest.call_status("procedure_card", CARD)
    _, chk_txt = await rest.call_status("check_conditions", CHK)
    async with raw.session() as s:
        raw_txt = (await s.call("raw_list", {})).text
    card_b, chk_b = len(card_txt.encode()), len(chk_txt.encode())
    walk = {"steps_n": 1, "bytes_total": 300, "stop": "enough", "fallback": False, "jev_calls": 2, "jev_ms_total": 10.0,
            "hub_ms_total": 4.0, "steps": [{"k": 1, "node": "unit_procedure:U5-140", "peek_bytes": 900,
                                            "expand": {"sections": ["aftercare"], "neighbors": [], "bytes": 300}}]}
    runs = [
        rec("r1", "Q001", BUILD["build_hash"],
            case1=[tc("raw_list", {}, len(json.dumps(json.loads(raw_txt), ensure_ascii=False).encode()),
                      intent_passed=None, stray=False)],
            case2=[tc("procedure_card", CARD, card_b, intent_passed=True, stray=False, truncated=False),
                   tc("check_conditions", CHK, chk_b // 2),  # 옛 기록 꼴(칸 없음) · 전체보다 작음 · 마커 없음 → unknown
                   tc("Bash", {"command": "jq"}, 68, is_error=True)],  # 옛 기록 stray
            case3={"walk": walk}),
        rec("r2", "Q002", BUILD["build_hash"],
            case2=[tc("check_conditions", CHK, 300, intent_passed=False, stray=False, truncated=True)]),
        rec("r3", "Q003", "other0build0hash", case2=[tc("procedure_card", CARD, card_b, intent_passed=True, stray=False)]),
    ]
    rd = tmp_path / "runs"
    rd.mkdir()
    (rd / "runs.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in runs) + "\n", encoding="utf-8")
    fail = lambda q, st="valid": {"item": "accuracy", "claim": "c", "status": st,
                                  "evidence": {"path": "procedure-profiles.M9.aftercare", "quote": q}}
    qual = [{"run_id": "r1", "case": "case2", "question_no": "Q001",
             "judge": {"failures": [fail("fixture 소프웨이브 aftercare"), fail("원장에만 있는 사후관리 문장"),
                                    fail("x", "invalid")]}},
            {"run_id": "r1", "case": "case3", "question_no": "Q001",
             "judge": {"failures": [fail("fixture 소프웨이브 aftercare 2")]}},
            {"run_id": "r3", "case": "case2", "question_no": "Q003", "judge": {"failures": [fail("fixture 소프웨이브 aftercare")]}}]
    (rd / "quality.jsonl").write_text("\n".join(json.dumps(q, ensure_ascii=False) for q in qual) + "\n", encoding="utf-8")
    return rd, rest, raw, card_b, chk_b


async def test_counts(setup):
    rd, rest, raw, *_ = setup
    J = await V.run(rd, BATCH, None, rest=rest, raw=raw, label=BATCH)
    assert J["meta"]["hub_build_hash"] == BUILD["build_hash"] and J["build_compare"] == {"같음": 2, "다름": 1}
    t2 = J["truncation"]["case2/claude-cli"]
    assert t2["calls"] == 4 and t2["같음"] == 1 and t2["잘림(unknown)"] == 1 and t2["잘림(마커)"] == 1
    assert t2["대조 불가"] == 1 and t2["truncated"] == 2 and t2["by_tool"] == {"check_conditions": 2}
    assert J["truncation"]["case1/claude-cli"]["같음"] == 1  # 원장 MCP 는 들여쓰기 글 · 압축 꼴로 대조
    i2 = J["intent"]["case2/claude-cli"]
    assert (i2["passed"], i2["calls"], i2["given"]) == (2, 4, 4) and i2["values"] == {"I-03": 2, "none": 2}
    assert "case1/claude-cli" not in J["intent"]  # 원장 도구 = intent 해당 없음
    st = J["stray"]["case2/claude-cli"]
    assert (st["n"], st["names"], st["by_kind"]) == (1, {"Bash": 1}, {"bare_hub": 0, "foreign": 1})
    w = J["walk"]
    assert w["runs"] == 1 and w["stop"] == {"enough": 1} and w["peek_bytes_median"] == 900 and w["jev_calls"] == 2
    assert J["fairness"]["case3/claude-cli"] == {"runs": 1, "median": 1200}  # bytes_total + peek
    c2 = J["c_ratio"]["case2/claude-cli"]
    assert c2["fail_rows"] == 3 and c2["C"] == 1 and c2["not_comparable"] == 1  # r3 = 빌드 다름
    assert J["c_ratio"]["case3/claude-cli"]["C"] == 1  # 걸음 expand 재호출 글에 있음
    assert J["invalid_excluded"] == {"case2": 1} and J["c_case23"] == {"C": 2, "fail_rows": 4}


async def test_deterministic_and_out_of_repo(setup, tmp_path):
    rd, rest, raw, *_ = setup
    outs = []
    for k in range(2):
        J = await V.run(rd, BATCH, None, rest=rest, raw=raw, label=BATCH)
        md, js = V.write(J, tmp_path / f"o{k}", BATCH)
        d = json.loads(js.read_text(encoding="utf-8"))
        assert d["meta"].pop("ran_at")
        outs.append((md.read_text(encoding="utf-8"), d))
    assert outs[0] == outs[1]
    assert "잘림(unknown)" in outs[0][0] and "C 2 = 50.0%" in outs[0][0]


async def test_no_hub_all_not_comparable(setup):
    rd, *_ = setup
    J = await V.run(rd, BATCH, None, rest=None, raw=None, label=BATCH)
    assert J["truncation"]["case2/claude-cli"]["대조 불가"] == 4 and J["c_case23"]["C"] == 0


def test_main_refuses_repo_out(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "get_settings", lambda: make_settings(tmp_path))
    with pytest.raises(SystemExit):
        V.main(["--batch", BATCH, "--no-hub", "--out", str(ROOT / "build-out")])


def test_forms_integral_floats_not_truncation():
    """REST 「14.0」 · MCP 가 넘긴 「14」 — 같은 값이라 잘림 아님(rerun-v4 pair_check · compare 실측 12~96B 차)."""
    rest = json.dumps({"days": [14.0, 14.0], "x": "가"}, ensure_ascii=False, separators=(",", ":"))
    mcp = json.dumps({"days": [14, 14], "x": "가"}, ensure_ascii=False, separators=(",", ":"))
    assert len(mcp.encode()) in V.forms(rest) and len(mcp.encode()) < len(rest.encode())


def _w(start, steps, **kw):
    return {"cases": {"case3": {"trace": {"walk": {"start_node": start, "steps": steps, "steps_n": len(steps), **kw}}}}}


def test_walk_summary_and_cover34():
    """§4-4 v2.2~v2.4 걸음 칸 · 34 행 몫(fixture = walk_cover34.json · S 같은 행만 분모)."""
    rows = {r["no"]: r for r in json.loads(V.COVER34_FILE.read_text(encoding="utf-8"))["rows"]}
    q106, q055 = rows["Q106"], rows["Q055"]
    tgt = q106["targets"]["key_family"][0]
    runs = [
        {**_w(q106["start"], [{"node": q106["start"], "hop": 0, "jev": {"picked_neighbors": [tgt], "filtered_by_budget": ["x"]},
                               "expand": {"fact_ids": [], "calls": [{"bytes": 10, "excluded": True}]}},
                              {"node": tgt, "hop": 1, "expand": {"fact_ids": ["f1"], "calls": [{"bytes": 5}]}},
                              {"node": "n2", "hop": 2, "expand": {"fact_ids": ["f2"]}},
                              {"node": q106["start"], "peek_reused": True}],
              stop="steps", bytes_total=900, step1_options=[tgt, "section:overview"], bytes_mismatch=1,
              full={"bytes": 4000, "over_budget": False, "cut_unfetched_ids": ["c1"]},
              list={"calls": 1, "bytes": 700, "ms": 3.0}), "question_no": "Q106"},
        {**_w("family:OTHER", [{"node": "family:OTHER", "hop": 0}], stop="budget", fallback=True,
              fallback_reason="no_facts", step1_options=["section:disclosure"]), "question_no": "Q055"},
        {**_w(q055["start"], [], stop="empty", fallback=True, fallback_reason="no_start"), "question_no": "Q001"},
    ]
    w = V.walk_summary(runs)
    assert (w["runs"], w["walked"], w["fallback"], w["fallback_reasons"]) == (3, 1, 2, {"no_facts": 1, "no_start": 1})
    assert w["steps_median"] == 1 and w["budget_stop"] == 1 and w["budget_stop_ratio"] == round(1 / 3, 4)
    assert (w["neighbor_fact_reach"], w["neighbor_peek_reach"], w["hop2_fact_reach"]) == (1, 1, 1)
    assert (w["excluded"], w["filtered_by_budget"], w["bytes_mismatch"], w["peek_reused"]) == (1, 1, 1, 1)
    assert (w["full_bytes_median"], w["full_cut_unfetched"], w["list_calls"], w["list_bytes_median"]) == (4000, 1, 1, 700)
    c = V.cover34(runs)
    one, zero = c["one_hop"], c["zero_hop"]
    assert (one["rows"], one["denominator"], one["shown"], one["picked"], one["got"]) == (8, 1, 1, 1, 1)
    assert zero["denominator"] == 0 and zero["other_start"] == ["Q055"] and zero["other_start_shown"] == 1
    assert "Q078" in zero["not_run"] and c["sum_shown"] == 1
