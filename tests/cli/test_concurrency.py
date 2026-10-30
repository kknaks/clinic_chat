"""10-30 피드백 — 열마다 채점이 바로 · 채점과 다음 질문이 서로 막지 않음 · 화면 상태가 풀림(새로고침 없이 연속)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clinic_chat.llm import cli
from clinic_chat.server import create_app
from tests import mock_server
from tests.cli.test_cli import CLAUDE_ANSWER, CLAUDE_TOOLS, CODEX, FAKE
from tests.cli.test_runner_cli import EXTRACT, JUDGE

ROOT = Path(__file__).resolve().parent.parent.parent
NODE = shutil.which("node")


@pytest.fixture
def slow_route(tmp_path, monkeypatch):
    d = tmp_path / "route"
    d.mkdir()
    for name, text in (("answer.jsonl", CLAUDE_ANSWER), ("tools.jsonl", CLAUDE_TOOLS), ("extract.jsonl", EXTRACT),
                       ("judge.jsonl", JUDGE), ("codex.jsonl", CODEX)):
        (d / name).write_text(text, encoding="utf-8")
    (d / "tools.sleep").write_text("0.06")  # case1 · 2(도구 루프) 느림 → case3 가 먼저 끝남
    (d / "answer.sleep").write_text("0.005")
    (d / "judge.sleep").write_text("0.25")  # 채점 느림(Opus)
    monkeypatch.setenv("FAKE_CLI_ROUTE", str(d))
    monkeypatch.setattr(cli, "CACHE_DIR", tmp_path / "cache")
    auth_file = tmp_path / "codex-auth.json"
    auth_file.write_text("{}")
    monkeypatch.setattr(cli, "CODEX_AUTH", auth_file)
    return d


def sse_until(c, rid, stop: str) -> list[tuple[str, dict]]:
    out, ev = [], None
    with c.stream("GET", f"/api/runs/{rid}/events") as r:
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and ev:
                out.append((ev, json.loads(line.split(":", 1)[1].strip())))
                if ev == stop:
                    break
                ev = None
    return out


def app(tmp_path):
    deps, hub = mock_server.build(str(tmp_path / "runs"), realtime=False)
    deps.settings = deps.settings.model_copy(update={"claude_cli_bin": str(FAKE / "claude"),
                                                     "codex_cli_bin": str(FAKE / "codex"), "judge_provider": "claude-cli"})
    return TestClient(create_app(deps, before_start=hub.start))


def test_per_column_grading_starts_when_column_done(tmp_path, slow_route):
    with app(tmp_path) as c:
        rid = c.post("/api/ask", json={"question": "소프웨이브 어떻게 해요?", "provider": "claude-cli"}).json()["run_id"]
        evs = sse_until(c, rid, "run_done")
    kinds = [(e, d.get("case")) for e, d in evs]
    first_grade = kinds.index(("case_quality_start", "case3"))
    assert first_grade < kinds.index(("case_done", "case1"))  # case3 채점이 case1 끝나기 전에 시작(세 열을 기다리지 않음)
    assert kinds.index(("answers_done", None)) < kinds.index(("run_done", None))
    for case in ("case1", "case2", "case3"):
        assert ("case_quality_done", case) in kinds and ("judge", case) in kinds
    judges = [d for e, d in evs if e == "judge"]
    assert all(j["mode"] == "absolute" and j["pass_count"] == 5 for j in judges)


def test_next_question_not_blocked_by_grading(tmp_path, slow_route):
    with app(tmp_path) as c:
        r1 = c.post("/api/ask", json={"question": "첫 질문", "provider": "claude-cli"}).json()["run_id"]
        sse_until(c, r1, "answers_done")  # 화면은 여기서 풀림
        assert next(r for r in c.get("/api/runs").json()["runs"] if r["run_id"] == r1)["grading"] is True
        t = time.monotonic()
        r2 = c.post("/api/ask", json={"question": "둘째 질문", "provider": "claude-cli"}).json()["run_id"]
        e2 = sse_until(c, r2, "answers_done")
        took = time.monotonic() - t
        runs = {r["run_id"]: r for r in c.get("/api/runs").json()["runs"]}
        assert e2[-1][0] == "answers_done" and took < 3.0
        assert runs[r1]["grading"] is True  # 앞 질문 채점이 아직 도는 동안 둘째 질문 답이 끝남 — 서로 안 막음
        sse_until(c, r2, "run_done")
        for _ in range(100):
            runs = {r["run_id"]: r for r in c.get("/api/runs").json()["runs"]}
            if not runs[r1]["grading"]:
                break
            time.sleep(0.1)
        assert runs[r1]["graded"] and runs[r2]["graded"] and not runs[r1]["grading"]


def test_cli_concurrency_cap_queues(tmp_path, slow_route, monkeypatch):
    monkeypatch.setattr(cli, "MAX_CONCURRENT", 1)
    monkeypatch.setattr(cli, "_SEMS", {})
    with app(tmp_path) as c:
        cli.MAX_CONCURRENT = 1
        rid = c.post("/api/ask", json={"question": "q", "provider": "claude-cli"}).json()["run_id"]
        sse_until(c, rid, "answers_done")
        rec = c.get(f"/api/runs/{rid}").json()["record"]
    stages = [s["name"] for case in rec["cases"].values() for s in case["timing"]["stages"]]
    assert "cli_queue" in stages  # 상한 1 → 세 열 중 둘은 기다림(cli_queue 단계로 기록)


def replay(script: str):
    return json.loads(subprocess.run([NODE, "-e", script, str(ROOT / "web" / "state.js")], capture_output=True,
                                     text=True, check=True).stdout)


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_send_state_two_questions_without_reload():
    """보내기 버튼 — 답 세 열이 끝나면(answers_done) 풀림 · 채점 중이어도 다음 질문 · 끊김 · 오류도 풀림."""
    out = replay(
        "const S=require(process.argv[1]);const r=[];"
        "let s=S.initial('r1','q1');r.push(S.sendState(s,true).disabled);r.push(S.sendState(s,false).disabled);"
        "S.reduce(s,'case_done',{case:'case3',timing:{stages:[]}});"
        "S.reduce(s,'case_quality_start',{case:'case3'});r.push(S.sendState(s,false).disabled);"
        "S.reduce(s,'answers_done',{run_id:'r1',auto_quality:true});r.push(S.sendState(s,false).disabled);"
        "r.push(s.grading, s.cases.case3.grading);"
        "S.reduce(s,'judge',{case:'case3',mode:'absolute',pass_count:4,items:{}});"
        "S.reduce(s,'case_quality_done',{case:'case3'});r.push(s.grading, s.cases.case3.judge.pass_count);"
        "let s2=S.initial('r2','q2');r.push(S.sendState(s2,false).disabled);"
        "s2.stream_lost=true;r.push(S.sendState(s2,false).disabled);"
        "let s3=S.initial('r3','q3');S.reduce(s3,'error',{message:'x'});S.reduce(s3,'run_done',{});"
        "r.push(S.sendState(s3,false).disabled);r.push(S.sendState(s2,false).label);"
        "console.log(JSON.stringify(r))")
    assert out == [True, True, True, False, True, True, False, 4, True, False, False, "보내기"]


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_legend_gap_and_thinking_labels():
    out = replay(
        "const S=require(process.argv[1]);console.log(JSON.stringify(S.legendItems(["
        "{name:'cli_boot',start_ms:0,end_ms:700},{name:'claude#1',start_ms:700,end_ms:2000,thinking:true,thinking_ms:900},"
        "{name:'(측정 밖)#1',start_ms:2000,end_ms:2400,gap:true},{name:'claude#2',start_ms:2400,end_ms:null,thinking:true}])))")
    assert [(x["kind"], x["label"], x["dur"]) for x in out] == [
        ("boot", "기동", "700ms"), ("claude", "claude#1 · 생각 900ms", "1.3s"), ("gap", "(측정 밖)", "400ms"),
        ("claude", "claude#2 · 생각 중", "…")]
