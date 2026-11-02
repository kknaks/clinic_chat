"""Phase 7-1 — 모의 판 E2E: 질문 1 → 3열 · ① · ② · 기록 · 사람 확인 + 세 케이스 같은 조건."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from clinic_chat import prompts
from clinic_chat.server import create_app
from tests import mock_server
from tests.phase6.test_screen import ask


def test_mock_e2e_one_question(tmp_path):
    runs = tmp_path / "runs-e2e"
    deps, hub = mock_server.build(str(runs), realtime=False)
    with TestClient(create_app(deps, before_start=hub.start)) as c:
        rid, _ = ask(c, "울쎄라 소프웨이브 뭐가 달라요?")
        rec = c.post(f"/api/runs/{rid}/quality").json()["record"]  # 「채점」 버튼
        for name in ("case1", "case2", "case3"):
            r = rec["cases"][name]
            assert r["error"] is None and r["answer"] and r["timing"]["total_ms"] > 0
            assert "not_found" in r["word_check"] and r["judge"]["pass_count"] >= 0
        assert rec["cases"]["case3"]["trace"]["hub"]["tool"] == "compare"
        assert rec["graded_cases"] == ["case1", "case2", "case3"] and rec["judge_mode"] == "absolute"
        assert c.post(f"/api/runs/{rid}/human_check", json={"case": "case3", "verdict": "틀림", "note": "e2e"}).json()
    lines = (runs / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["run_id"] == rid
    assert json.loads((runs / "human_checks.jsonl").read_text(encoding="utf-8"))["verdict"] == "틀림"

    # 세 케이스 같은 조건 — 모델 · between_tools · effort · 공통 프롬프트 판
    sonnet = [p for p in deps.anthropic.calls if p["model"].startswith("claude-sonnet")]
    common = prompts.load("common").rstrip()
    by_case = {}
    for p in sonnet:
        sys_text = p["system"][0]["text"]
        assert p["system"][0]["cache_control"] == {"type": "ephemeral"}  # 시스템(+도구) 캐시 — 세 케이스 같음
        by_case.setdefault(sys_text.split("## 자료")[1][:12], []).append(p)
        assert p["model"] == rec["versions"]["answer_model"] == "claude-sonnet-5-5"
        assert p["thinking"] == {"type": "between_tools"} and p["output_config"] == {"effort": "high"}
        assert sys_text.startswith(common)
    assert len(by_case) == 3
    assert rec["settings"]["thinking"] == "between_tools" and rec["settings"]["effort"] == "high"
    assert all(rec["cases"][x]["timing"]["t0"] == 0.0 for x in rec["cases"])


async def test_rest_list_query_is_comma(deps_factory, hub):
    """허브 REST 목록 쿼리 = 쉼표(실물 대조 10-30 — 반복 키는 마지막만 남음)."""
    deps = deps_factory()
    await deps.hub_rest.call("procedure_card", {"id": "unit_procedure:U5-140", "sections": ["overview", "ops"]})
    await deps.hub_rest.vocab(["intent", "menu"])
    got = {t: a for via, t, a in hub.calls if via == "gold-rest"}
    assert got["procedure_card"]["sections"] == ["overview", "ops"]
    assert got["vocab"]["kinds"] == ["intent", "menu"]
