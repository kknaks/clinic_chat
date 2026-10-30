"""CLI provider 를 실행기 · 서버에서 — 질문마다 provider · 기록 칸 · CLI 판 자동 채점(Claude CLI Opus) · 구독 아니면 세 열 오류."""

from __future__ import annotations

import json

import pytest

from clinic_chat import runner
from clinic_chat.llm import cli
from tests.cli.test_cli import CLAUDE_ANSWER, CLAUDE_TOOLS, CODEX, FAKE, RESULT, init, j, turn
from tests.fake_anthropic import FakeAnthropic, answer_policy
from tests.fake_jev import FakeJev

EXTRACT = j(init(), *turn([""]), {**RESULT, "structured_output": {"names": [{"text": "울쎄라", "kind": "시술"}]}})
JUDGE = j(init("raw"), *turn(["확인"]), {**RESULT, "structured_output": {"items": [
    {"item": i, "verdict": "통과", "claim": "", "evidence": None}
    for i in ("accuracy", "safety", "candidates", "uncertainty", "on_topic")]}})


@pytest.fixture
def route(tmp_path, monkeypatch):
    d = tmp_path / "route"
    d.mkdir()
    for name, text in (("answer.jsonl", CLAUDE_ANSWER), ("tools.jsonl", CLAUDE_TOOLS), ("extract.jsonl", EXTRACT),
                       ("judge.jsonl", JUDGE), ("codex.jsonl", CODEX)):
        (d / name).write_text(text, encoding="utf-8")
    log = tmp_path / "cli-log.jsonl"
    monkeypatch.setenv("FAKE_CLI_ROUTE", str(d))
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.setattr(cli, "CACHE_DIR", tmp_path / "cache")
    auth_file = tmp_path / "codex-auth.json"
    auth_file.write_text("{}")
    monkeypatch.setattr(cli, "CODEX_AUTH", auth_file)
    return log


def cli_deps(deps_factory, **kw):
    deps = deps_factory(anthropic=FakeAnthropic(answer_policy), claude_cli_bin=str(FAKE / "claude"),
                        codex_cli_bin=str(FAKE / "codex"), judge_provider="claude-cli", **kw)
    deps.jev = FakeJev(intent="I-02").client(deps.settings)
    return deps


async def test_claude_cli_run_record_and_auto_quality(deps_factory, route):
    deps = cli_deps(deps_factory)
    deps.vocab = await deps.hub_rest.vocab()
    events: list[tuple[str, dict]] = []
    rec = await runner.execute(deps, "소프웨이브 어떻게 해요?", lambda e, d: events.append((e, d)), provider="claude-cli")
    assert rec["provider"] == "claude-cli"
    v = rec["versions"]
    assert (v["answer_provider"], v["answer_model"], v["answer_effort"]) == ("claude-cli", "claude-sonnet-5-5", "medium")
    assert v["auth_kind"] == "subscription" and v["judge_provider"] == "claude-cli" and v["cli_version"]
    assert v["answer_model_observed"] == "claude-sonnet-5-5"
    st = rec["settings"]
    assert st["tool_loop_max"] is None and st["thinking"] == "cli-default" and st["max_budget_usd"] == 1.0
    for c in runner.CASES:
        r = rec["cases"][c]
        assert r["error"] is None, (c, r["error"])
        assert r["timing"]["ttft_kind"] == "token" and r["timing"]["cli_boot_ms"] is not None
        assert r["cost"]["billed"] == "subscription"
    assert [x["tool"] for x in rec["cases"]["case2"]["trace"]["tool_calls"]] == ["recommend"]
    assert rec["cases"]["case3"]["trace"]["hub"]["tool"] == "procedure_card"  # 앞 단계는 앱 그대로
    # CLI 판 = 자동 채점(Claude CLI Opus) · 원장 도구 = 로컬 stdio MCP
    assert all(rec["cases"][c]["judge"]["pass_count"] == 5 for c in runner.CASES)  # 열마다 절대 채점
    assert all(rec["cases"][c]["word_check"]["provider"] == "claude-cli" for c in runner.CASES)
    calls = [json.loads(x) for x in route.read_text().splitlines()]
    judges = [x for x in calls if "--json-schema" in x["argv"] and '"items"' in x["argv"][x["argv"].index("--json-schema") + 1]
              and '"names"' not in x["argv"][x["argv"].index("--json-schema") + 1]]
    assert len(judges) == 3  # 열마다 한 번(답 하나씩)
    for jc in judges:
        for bad in ("case1", "case2", "case3", "claude-cli", "codex"):
            assert bad not in jc["stdin"]  # 가림
    judge = judges[0]
    a = judge["argv"]
    assert a[a.index("--model") + 1] == "claude-opus-5-5" and a[a.index("--effort") + 1] == "medium"
    cfg = json.loads(a[a.index("--mcp-config") + 1])["mcpServers"]["raw"]
    assert cfg["type"] == "stdio" and cfg["args"] == ["-m", "clinic_chat.quality.raw_mcp"] and "RAW_DIR" in cfg["env"]
    extract = next(x for x in calls if "--json-schema" in x["argv"] and '"names"' in x["argv"][x["argv"].index("--json-schema") + 1])
    assert extract["argv"][extract["argv"].index("--effort") + 1] == "low"
    assert ("provider", {"provider": "claude-cli", "model": "claude-sonnet-5-5", "effort": "medium", "billed": "subscription",
                         "service_tier": None, "auth": {"kind": "subscription", "detail": "claude.ai · max",
                                                        "cli_version": "2.1.284 (Claude Code)"}}) in events


async def test_codex_cli_run(deps_factory, route):
    deps = cli_deps(deps_factory)
    deps.vocab = await deps.hub_rest.vocab()
    rec = await runner.execute(deps, "소프웨이브 어떻게 해요?", lambda e, d: None, provider="codex-cli", quality=False)
    assert rec["versions"]["answer_model"] == "gpt-5.6-terra" and rec["versions"]["answer_effort"] == "medium"
    for c in runner.CASES:
        assert rec["cases"][c]["error"] is None and rec["cases"][c]["timing"]["ttft_kind"] == "message"
    assert rec["settings"]["max_budget_usd"] is None


async def test_not_subscription_all_columns_error(deps_factory, route, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_AUTH", "api")
    deps = cli_deps(deps_factory)
    rec = await runner.execute(deps, "q", lambda e, d: None, provider="claude-cli")
    assert all(rec["cases"][c]["error"].startswith("인증") for c in runner.CASES)
    assert rec["judge_order"] == [] and rec["versions"]["auth_kind"] == "api_key"
    calls = [json.loads(x) for x in route.read_text().splitlines()]
    assert not [x for x in calls if "-p" in x["argv"]]  # 모델 호출 0


async def test_sdk_still_default_button_quality(deps_factory):
    deps = deps_factory(anthropic=FakeAnthropic(answer_policy))
    deps.jev = FakeJev(intent="I-02").client(deps.settings)
    deps.vocab = await deps.hub_rest.vocab()
    rec = await runner.execute(deps, "소프웨이브 어떻게 해요?", lambda e, d: None, provider="sdk")
    assert rec["provider"] == "sdk" and rec["judge_order"] == []  # sdk 판은 버튼(기본 끔)
    with pytest.raises(ValueError):
        await runner.execute(deps, "q", lambda e, d: None, provider="gpt-api")


def test_server_providers_and_ask(tmp_path, route, monkeypatch):
    from fastapi.testclient import TestClient

    from clinic_chat.server import create_app
    from tests import mock_server
    from tests.phase6.test_screen import sse_events

    deps, hub = mock_server.build(str(tmp_path / "runs"), realtime=False)
    deps.settings = deps.settings.model_copy(update={"claude_cli_bin": str(FAKE / "claude"),
                                                     "codex_cli_bin": str(FAKE / "codex"), "judge_provider": "claude-cli"})
    with TestClient(create_app(deps, before_start=hub.start)) as c:
        d = c.get("/api/providers").json()
        by = {p["provider"]: p for p in d["providers"]}
        assert by["claude-cli"]["available"] and by["claude-cli"]["model"] == "claude-sonnet-5-5"
        assert by["codex-cli"]["available"] and by["codex-cli"]["model"] == "gpt-5.6-terra"
        assert d["judge"] == {"provider": "claude-cli", "model": "claude-opus-5-5"}
        assert c.post("/api/ask", json={"question": "q", "provider": "nope"}).status_code == 422
        rid = c.post("/api/ask", json={"question": "소프웨이브 어떻게 해요?", "provider": "codex-cli"}).json()["run_id"]
        with c.stream("GET", f"/api/runs/{rid}/events") as r:
            evs = sse_events("".join(r.iter_text()))
        assert ("provider" in [e for e, _ in evs]) and evs[-1][0] == "run_done"
        row = c.get("/api/runs").json()["runs"][0]
        assert row["provider"] == "codex-cli" and row["model"] == "gpt-5.6-terra"
        b = c.get("/api/batches").json()
        assert set(b["config_missing"]) == {"BATCH_GAP_SEC", "BATCH_RETRIES", "BATCH_LIMIT_WAIT_SEC"}
        assert c.get("/api/questions").status_code == 409  # QUESTIONS_MD 없음
