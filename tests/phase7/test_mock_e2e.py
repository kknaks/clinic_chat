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
        rec = c.get(f"/api/runs/{rid}").json()["record"]
        for name in ("case1", "case2", "case3"):
            r = rec["cases"][name]
            assert r["error"] is None and r["answer"] and r["timing"]["total_ms"] > 0
            assert "not_found" in r["word_check"] and r["judge"]["pass_count"] >= 0
        assert rec["cases"]["case3"]["trace"]["hub"]["tool"] == "compare"
        assert sorted(rec["judge_order"]) == ["case1", "case2", "case3"]
        assert c.post(f"/api/runs/{rid}/human_check", json={"case": "case3", "verdict": "틀림", "note": "e2e"}).json()
    lines = (runs / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["run_id"] == rid
    assert json.loads((runs / "human_checks.jsonl").read_text(encoding="utf-8"))["verdict"] == "틀림"

    # 세 케이스 같은 조건 — 모델 · between_tools · effort · 공통 프롬프트 판
    sonnet = [p for p in deps.anthropic.calls if p["model"].startswith("claude-sonnet")]
    common = prompts.load("common").rstrip()
    by_case = {}
    for p in sonnet:
        by_case.setdefault(p["system"].split("## 자료")[1][:12], []).append(p)
        assert p["model"] == rec["versions"]["answer_model"] == "claude-sonnet-5-5"
        assert p["thinking"] == {"type": "between_tools"} and p["output_config"] == {"effort": "high"}
        assert p["system"].startswith(common)
    assert len(by_case) == 3
    assert rec["settings"]["thinking"] == "between_tools" and rec["settings"]["effort"] == "high"
    assert all(rec["cases"][x]["timing"]["t0"] == 0.0 for x in rec["cases"])
