"""CLI provider — 모의 CLI(tests/fake_cli) + 합성 stream-json · JSONL 픽스처(cli-research §2 꼴 · 참고 설계 ingest 꼴).

실물 claude · codex 를 부르지 않는다(conftest 가 막음). 자식 env 키 지움 · 구독 확인 · 격리 플래그 · 이벤트 → 단계 · ttft 종류 ·
도구 호출 · 한도 신호 · 구조화 출력 · 채점 stdio MCP 를 본다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from clinic_chat.llm import auth, cli
from clinic_chat.llm.cli import CliSpec, McpAttach
from clinic_chat.llm.cli_process import SCRUB_ENV, child_env
from clinic_chat.timing import CaseTimer, Clock

FAKE = Path(__file__).resolve().parent.parent / "fake_cli"
TOOL_RESULT = json.dumps({"build": {"build_hash": "fixture0build0h1"},
                          "cards": [{"id": "unit_procedure:U5-102", "family": "family:M9"}]}, ensure_ascii=False)


def j(*evs) -> str:
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in evs)


def init(mcp=None, key="none", tools=None):
    return {"type": "system", "subtype": "init", "model": "claude-sonnet-5-5", "apiKeySource": key,
            "claude_code_version": "2.1.284", "permissionMode": "bypassPermissions",
            "tools": tools if tools is not None else ([f"mcp__{mcp}__resolve"] if mcp else []),
            "mcp_servers": [{"name": mcp, "status": "connected"}] if mcp else []}


def se(ev):
    return {"type": "stream_event", "event": ev}


def turn(texts, stop="end_turn", usage=None):
    return [se({"type": "message_start", "message": {"usage": usage or {"input_tokens": 120, "cache_read_input_tokens": 30}}}),
            *[se({"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}) for t in texts],
            se({"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 15}}),
            se({"type": "message_stop"})]


RESULT = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "duration_ms": 900,
          "duration_api_ms": 700, "total_cost_usd": 0.0123,
          "usage": {"input_tokens": 120, "output_tokens": 15, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 30},
          "modelUsage": {"claude-sonnet-5-5": {"input_tokens": 120, "output_tokens": 15}}, "result": "안녕하세요 답"}

CLAUDE_ANSWER = j(init(), *turn(["안녕", "하세요 ", "답"]),
                  {"type": "assistant", "message": {"content": [{"type": "text", "text": "안녕하세요 답"}]}},
                  {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour",
                                                                   "utilization": 0.2, "resetsAt": 1890000000}},
                  RESULT)
CLAUDE_TOOLS = j(init("hub"), *turn(["찾아볼게요. "], stop="tool_use"),
                 {"type": "assistant", "message": {"content": [
                     {"type": "text", "text": "찾아볼게요. "},
                     {"type": "tool_use", "id": "tu1", "name": "mcp__hub__recommend", "input": {"concerns": ["concern:P2"]}}]}},
                 {"type": "user", "message": {"content": [
                     {"type": "tool_result", "tool_use_id": "tu1", "content": [{"type": "text", "text": TOOL_RESULT}]}]}},
                 *turn(["울쎄라를 ", "권합니다."]), {**RESULT, "num_turns": 2})
CODEX = j({"type": "thread.started", "thread_id": "th1"}, {"type": "turn.started"},
          {"type": "item.started", "item": {"id": "i1", "type": "mcp_tool_call", "server": "hub", "tool": "recommend",
                                            "arguments": {"concerns": ["concern:P2"]}, "status": "in_progress"}},
          {"type": "item.completed", "item": {"id": "i1", "type": "mcp_tool_call", "server": "hub", "tool": "recommend",
                                              "arguments": {"concerns": ["concern:P2"]}, "status": "completed",
                                              "result": json.loads(TOOL_RESULT)}},
          {"type": "item.completed", "item": {"id": "i2", "type": "reasoning", "text": ""}},
          {"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": "울쎄라를 권합니다."}},
          {"type": "turn.completed", "usage": {"input_tokens": 900, "cached_input_tokens": 100, "output_tokens": 40,
                                               "reasoning_output_tokens": 12}})


@pytest.fixture
def fake(tmp_path, monkeypatch):
    log = tmp_path / "cli-log.jsonl"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    monkeypatch.setattr(cli, "CACHE_DIR", tmp_path / "cache")
    codex_auth = tmp_path / "codex-auth.json"
    codex_auth.write_text("{}")
    monkeypatch.setattr(cli, "CODEX_AUTH", codex_auth)

    def lines(text: str) -> None:
        p = tmp_path / "lines.jsonl"
        p.write_text(text, encoding="utf-8")
        monkeypatch.setenv("FAKE_CLI_LINES", str(p))

    def calls() -> list[dict]:
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    return lines, calls


def spec(kind: str) -> CliSpec:
    if kind == "claude":
        return CliSpec("claude-cli", str(FAKE / "claude"), "claude-sonnet-5-5", "medium", 30, max_budget_usd=1.0)
    return CliSpec("codex-cli", str(FAKE / "codex"), "gpt-5.6-terra", "medium", 30)


def timer() -> CaseTimer:
    c = Clock()
    c.start()
    return CaseTimer(c)


def test_child_env_scrubs_keys():
    base = {k: "secret" for k in SCRUB_ENV} | {"PATH": "/bin", "HOME": "/h"}
    env = child_env({"ANTHROPIC_API_KEY": "x", "CODEX_HOME": "/c"}, base=base)
    assert not any(k in env for k in SCRUB_ENV)
    assert env["PATH"] == "/bin" and env["CODEX_HOME"] == "/c"
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CODEX_API_KEY", "OPENAI_API_KEY"):
        assert k in SCRUB_ENV


def test_auth_parsers():
    assert auth.parse_claude_status(json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                                "apiProvider": "firstParty", "subscriptionType": "max"})).ok
    a = auth.parse_claude_status(json.dumps({"loggedIn": True, "authMethod": "apiKey", "apiProvider": "firstParty"}))
    assert not a.ok and a.kind == "api_key"
    assert not auth.parse_claude_status(json.dumps({"loggedIn": False})).ok
    assert auth.parse_codex_status("Logged in using ChatGPT").ok
    assert auth.parse_codex_status("Logged in using an API key - sk-…").kind == "api_key"


async def test_claude_answer_isolated_and_measured(fake, monkeypatch):
    lines, calls = fake
    lines(CLAUDE_ANSWER)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-pass")
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-pass")
    out: list[str] = []
    r = await cli.run(spec("claude"), system="시스템 프롬프트", prompt="질문", timer=timer(), on_text=out.append)
    assert r["error"] is None and r["answer"] == "안녕하세요 답" == "".join(out)
    t = r["timing"]
    assert t["ttft_kind"] == "token" and t["ttft_ms"] is not None and t["cli_boot_ms"] is not None
    assert t["ttft_ms_ex_boot"] == round(t["ttft_ms"] - t["cli_boot_ms"], 1)
    assert [s["name"] for s in t["stages"]] == ["cli_boot", "claude#1"] and t["llm_round_trips"] == 1
    assert r["cost"]["usd_estimate"] == 0.0123 and r["cost"]["billed"] == "subscription"
    c = r["trace"]["cli"]
    assert c["auth_kind"] == "subscription" and c["observed_model"] == "claude-sonnet-5-5"
    assert c["rate_limits"][0]["rateLimitType"] == "five_hour" and c["tool_loop_max"] is None
    run_call = next(x for x in calls() if "-p" in x["argv"])
    a = run_call["argv"]
    for flag in ("--setting-sources", "--tools", "--strict-mcp-config", "--disable-slash-commands",
                 "--no-session-persistence", "--include-partial-messages", "--max-budget-usd"):
        assert flag in a
    assert a[a.index("--system-prompt") + 1] == "시스템 프롬프트" and a[a.index("--model") + 1] == "claude-sonnet-5-5"
    assert a[a.index("--effort") + 1] == "medium" and a[a.index("--tools") + 1] == "" and a[-1] == "질문"
    assert "--fallback-model" not in a and "--mcp-config" not in a
    assert "ANTHROPIC_API_KEY" not in run_call["env_keys"] and "OPENAI_API_KEY" not in run_call["env_keys"]
    assert ".cache" in run_call["cwd"] or "cache" in run_call["cwd"]  # 임시 cwd


async def test_claude_tool_loop_via_mcp(fake):
    lines, calls = fake
    lines(CLAUDE_TOOLS)
    r = await cli.run(spec("claude"), system="s", prompt="q", timer=timer(), on_text=lambda t: None,
                      mcp=McpAttach("hub", url="http://hub.test/procedure-hub/gold-mcp/mcp"), collect_node_ids=True)
    assert r["error"] is None
    names = [s["name"] for s in r["timing"]["stages"]]
    assert names == ["cli_boot", "claude#1", "tool:recommend#1", "claude#2"]
    call = r["trace"]["tool_calls"][0]
    assert call["tool"] == "recommend" and call["is_error"] is False and call["output_bytes"] > 0
    assert "unit_procedure:U5-102" in call["node_ids"]
    assert r["timing"]["ttft_ms"] < r["timing"]["ttft_answer_ms"]  # 앞 바퀴 글 → 마지막 바퀴 첫 글자
    a = next(x for x in calls() if "-p" in x["argv"])["argv"]
    cfg = json.loads(a[a.index("--mcp-config") + 1])
    assert cfg == {"mcpServers": {"hub": {"type": "http", "url": "http://hub.test/procedure-hub/gold-mcp/mcp"}}}
    assert a[a.index("--allowedTools") + 1] == "mcp__hub"


async def test_claude_mcp_not_connected_stops(fake):
    lines, _ = fake
    bad = init("hub")
    bad["mcp_servers"] = [{"name": "hub", "status": "failed"}]
    lines(j(bad, *turn(["답"]), RESULT))
    r = await cli.run(spec("claude"), system="s", prompt="q", timer=timer(), on_text=lambda t: None,
                      mcp=McpAttach("hub", url="http://hub.test/x"))
    assert "MCP 연결 안 됨" in r["error"] and r["answer"] == ""


async def test_claude_api_key_source_refused(fake):
    lines, _ = fake
    lines(j(init(key="ANTHROPIC_API_KEY"), *turn(["답"]), RESULT))
    r = await cli.run(spec("claude"), system="s", prompt="q", timer=timer(), on_text=lambda t: None)
    assert r["error"].startswith("구독 아님") and r["answer"] == ""


async def test_claude_rate_limit_rejected(fake):
    lines, _ = fake
    lines(j(init(), {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour",
                                                                      "resetsAt": 1890000000}},
            {**RESULT, "is_error": True, "subtype": "error_during_execution", "result": "limit"}))
    r = await cli.run(spec("claude"), system="s", prompt="q", timer=timer(), on_text=lambda t: None)
    assert r["trace"]["cli"]["rate_limited"] is True and "한도" in r["error"]


async def test_not_subscription_never_runs(fake, monkeypatch):
    lines, calls = fake
    lines(CLAUDE_ANSWER)
    monkeypatch.setenv("FAKE_CLI_AUTH", "api")
    r = await cli.run(spec("claude"), system="s", prompt="q", timer=timer(), on_text=lambda t: None)
    assert r["error"].startswith("인증") and r["trace"]["cli"]["auth_kind"] == "api_key"
    assert not [x for x in calls() if "-p" in x["argv"]]  # 모델 호출 없음
    monkeypatch.setenv("FAKE_CLI_AUTH", "apikey")
    r = await cli.run(spec("codex"), system="s", prompt="q", timer=timer(), on_text=lambda t: None)
    assert r["error"].startswith("인증") and not [x for x in calls() if "exec" in x["argv"]]


async def test_codex_isolated_message_ttft_and_tools(fake, tmp_path):
    lines, calls = fake
    lines(CODEX)
    out: list[str] = []
    r = await cli.run(spec("codex"), system="시스템", prompt="q", timer=timer(), on_text=out.append,
                      mcp=McpAttach("hub", url="http://hub.test/gold"), collect_node_ids=True)
    assert r["error"] is None and r["answer"] == "울쎄라를 권합니다." == "".join(out)
    t = r["timing"]
    assert t["ttft_kind"] == "message" and t["ttft_ms"] is not None
    assert [s["name"] for s in t["stages"]] == ["cli_boot", "codex#1", "tool:recommend#1"]
    assert r["trace"]["tool_calls"][0]["node_ids"] == ["unit_procedure:U5-102", "family:M9"]
    assert r["tokens"]["total"]["reasoning_output_tokens"] == 12 and r["trace"]["cli"]["thinking_seen"] is True
    assert r["trace"]["cli"]["item_types"] == ["mcp_tool_call", "reasoning", "agent_message"]
    c = next(x for x in calls() if "exec" in x["argv"])
    a = c["argv"]
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--json"):
        assert flag in a
    joined = " ".join(a)
    for kv in ("features.shell_tool=false", "features.unified_exec=false", 'web_search="disabled"',
               "tools.view_image=false", "features.plugins=false", 'shell_environment_policy.inherit="none"',
               'model_reasoning_effort="medium"', 'mcp_servers.hub.url="http://hub.test/gold"',
               "mcp_servers.hub.required=true", "project_doc_max_bytes=0", "model_instructions_file="):
        assert kv in joined
    assert a[a.index("-m") + 1] == "gpt-5.6-terra" and a[a.index("-s") + 1] == "read-only"
    home = Path(c["codex_home"])
    assert home.name == "codex-home" and (home / "auth.json").is_symlink()
    assert not (home / "config.toml").exists()


async def test_codex_usage_limit_marks_rate_limited(fake):
    lines, _ = fake
    lines(j({"type": "thread.started"}, {"type": "turn.started"},
            {"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again later."}}))
    r = await cli.run(spec("codex"), system="s", prompt="q", timer=timer(), on_text=lambda t: None)
    assert r["trace"]["cli"]["rate_limited"] is True and "usage limit" in r["error"]


async def test_codex_structured_output_file(fake, monkeypatch):
    lines, _ = fake
    lines(j({"type": "thread.started"}, {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "a", "type": "agent_message", "text": '{"names": []}'}},
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
    monkeypatch.setenv("FAKE_CLI_OUT", '{"names": [{"text": "울쎄라", "kind": "시술"}]}')
    r = await cli.run(spec("codex"), system="s", prompt="q", timer=timer(), on_text=lambda t: None,
                      schema={"type": "object"})
    assert r["structured"] == {"names": [{"text": "울쎄라", "kind": "시술"}]}


async def test_timeout_kills_group(fake, monkeypatch):
    lines, _ = fake
    lines(CLAUDE_ANSWER)
    monkeypatch.setenv("FAKE_CLI_SLEEP", "2")
    sp = spec("claude")
    sp.timeout_s = 0.5
    r = await cli.run(sp, system="s", prompt="q", timer=timer(), on_text=lambda t: None)
    assert "시간 초과" in r["error"]


async def test_raw_mcp_stdio_same_as_local(raw_dir):
    """채점 원장 도구 = 로컬 stdio MCP(D-4) — 같은 RawStore 결과."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from clinic_chat.quality.raw_tools import RawStore

    params = StdioServerParameters(command=sys.executable, args=["-m", "clinic_chat.quality.raw_mcp"],
                                   env={**os.environ, "RAW_DIR": str(raw_dir)})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        names = [t.name for t in (await s.list_tools()).tools]
        assert names == ["raw_list", "raw_get", "raw_search"]
        res = await s.call_tool("raw_get", {"path": "taxonomy.problem[3]"})
        data = res.structured_content
        bad = await s.call_tool("raw_get", {"path": "nope.x"})
    assert data == RawStore(raw_dir).raw_get("taxonomy.problem[3]")
    assert bad.is_error
