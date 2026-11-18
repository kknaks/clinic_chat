"""WP-1 — `claude -p` 격리 인자 · stdin · 자식 env(모의 CLI)."""

from __future__ import annotations

from clinic_chat.llm import cli
from clinic_chat.llm.cli import CliSpec, McpAttach, claude_args
from tests.conftest import read_log, stream

SCRUBBED = ("ANTHROPIC_API_KEY", "TYPESAFE_API_KEY", "GOLD_CSV_DIR", "LEDGER_CHECK_PATH", "QUESTIONS_CSV", "EVAL_SET_DIR")


async def test_isolation_stdin_and_env(fake_cli, monkeypatch):
    bin_, route, log = fake_cli
    for k in SCRUBBED:
        monkeypatch.setenv(k, "secret-fixture")
    (route / "answer.jsonl").write_text(stream("안녕", model="claude-x"), encoding="utf-8")
    r = await cli.run(CliSpec(bin_, "sonnet", "medium", 20), system="SYS", prompt="질문 본문")
    assert r["error"] is None and r["answer"] == "안녕" and r["observed_model"] == "claude-x"
    call = [x for x in read_log(log) if "-p" in x["argv"]][0]
    a = call["argv"]
    for flag in ("--setting-sources", "--tools", "--disallowedTools", "--disable-slash-commands", "--strict-mcp-config",
                 "--no-session-persistence", "--system-prompt"):
        assert flag in a
    assert a[a.index("--tools") + 1] == "" and a[a.index("--setting-sources") + 1] == ""
    assert "질문 본문" not in a and call["stdin"] == "질문 본문"
    assert not any(k in call["env_keys"] for k in SCRUBBED)
    assert "clinic-chat-cli-" in call["cwd"]


async def test_subscription_required(fake_cli, monkeypatch):
    bin_, route, _ = fake_cli
    monkeypatch.setenv("FAKE_CLI_AUTH", "api")
    r = await cli.run(CliSpec(bin_, "sonnet", "medium", 20), system="S", prompt="P")
    assert r["error"] and "인증" in r["error"]


def test_reasoning_args():
    off = claude_args(CliSpec("claude", "m", "high", 1, reasoning="off", lowest_effort="low"), "s", None, None)
    low = claude_args(CliSpec("claude", "m", "high", 1, reasoning="lowest_effort", lowest_effort="low"), "s", None, None)
    on = claude_args(CliSpec("claude", "m", "high", 1, reasoning="on"), "s", None, None)
    assert off[off.index("--effort") + 1] == "low" and "--settings" in off
    assert low[low.index("--effort") + 1] == "low" and "--settings" not in low
    assert on[on.index("--effort") + 1] == "high"
    assert cli.cli_env(CliSpec("claude", "m", "h", 1, reasoning="off"))["MAX_THINKING_TOKENS"] == "0"


def test_mcp_attach_tool_level_and_server_level():
    a = claude_args(CliSpec("c", "m", "h", 1), "s", McpAttach("hub", url="http://x", tools=("resolve", "pair")), None)
    assert a[a.index("--allowedTools") + 1] == "mcp__hub__resolve,mcp__hub__pair"
    b = claude_args(CliSpec("c", "m", "h", 1), "s", McpAttach("raw", command="py", args=["-m", "x"]), None)
    assert b[b.index("--allowedTools") + 1] == "mcp__raw" and '"type": "stdio"' in b[b.index("--mcp-config") + 1]


async def test_thinking_seen_and_exit_error(fake_cli, monkeypatch):
    bin_, route, _ = fake_cli
    (route / "answer.jsonl").write_text(stream("x", thinking=True), encoding="utf-8")
    r = await cli.run(CliSpec(bin_, "m", "h", 20), system="S", prompt="P")
    assert r["thinking_seen"] is True
    monkeypatch.setenv("FAKE_CLI_EXIT", "3")
    (route / "answer.jsonl").write_text("", encoding="utf-8")
    r2 = await cli.run(CliSpec(bin_, "m", "h", 20), system="S", prompt="P")
    assert r2["error"] and r2["answer"] == ""


def test_usage_summed_over_tool_round_trips():
    """코디 발견(C 토큰 0) — stream-json 의 왕복마다 usage(message_start 입력 · message_delta 출력 끝 값)를 모아 합친다."""
    import json

    from clinic_chat.columns.base import llm_input_bytes, llm_tokens
    from clinic_chat.timing import Timer

    def ev(e):
        return json.dumps({"type": "stream_event", "event": e})
    lines = [json.dumps({"type": "system", "subtype": "init", "model": "m", "apiKeySource": "none", "mcp_servers": []})]
    for i, (inp, out) in enumerate(((100, 5), (300, 9))):
        u0 = {"input_tokens": 2, "cache_creation_input_tokens": inp, "cache_read_input_tokens": 10, "output_tokens": 1}
        lines += [ev({"type": "message_start", "message": {"id": f"msg{i}", "usage": u0}}),
                  json.dumps({"type": "assistant", "message": {"id": f"msg{i}", "usage": u0, "content": []}}),
                  ev({"type": "message_delta", "delta": {}, "usage": {**u0, "output_tokens": out}}),
                  ev({"type": "message_stop"})]
    lines.append(json.dumps({"type": "result", "subtype": "success", "usage": {"input_tokens": 2, "output_tokens": 9}}))
    res = cli.new_result(CliSpec("x", "m", "low", 5))
    ing = cli.ClaudeIngest(res, Timer(), None)
    for ln in lines:
        ing.line(ln)
    assert res["token_rounds"] == [{"input_tokens": 2, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 10,
                                    "output_tokens": 5},
                                   {"input_tokens": 2, "cache_creation_input_tokens": 300, "cache_read_input_tokens": 10,
                                    "output_tokens": 9}]
    assert res["tokens"] == {"input_tokens": 4, "output_tokens": 14, "cache_creation_input_tokens": 400,
                             "cache_read_input_tokens": 20}
    assert llm_tokens(res) == {"llm": res["token_rounds"]}
    assert llm_tokens({"tokens": {"input_tokens": 1}}) == {"llm": [{"input_tokens": 1}]}  # 왕복 usage 없으면 result 것
    assert llm_input_bytes("ab", [{"output_bytes": 1000}, {"output_bytes": None}]) == 1002
