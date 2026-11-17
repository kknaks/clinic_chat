"""WP-3 — 상담 실행기(SPEC-004 T1 ~ T5 · CL2 ~ CL12) · 모의 CLI."""

from __future__ import annotations

import asyncio
import json
import uuid

from clinic_chat.consult.prompt import load
from clinic_chat.llm.chat import ChatConv
from clinic_chat.llm.chat_cli import FORBIDDEN_FLAGS, ChatCli, ChatCliSpec, chat_args
from clinic_chat.llm.cli import DISALLOWED_TOOLS
from tests.conftest import read_log, stream

PROMPT = None


def prompt():
    global PROMPT
    if PROMPT is None:
        from clinic_chat.config import ROOT

        PROMPT = load(ROOT / "prompts" / "consult.json")
    return PROMPT


def spec(bin_, tmp_path, **kw) -> ChatCliSpec:
    return ChatCliSpec(bin_, "sonnet", "haiku", "low", kw.pop("timeout_s", 20), tmp_path / "cwd", **kw)


EXPECT = ["-p", "--model", "sonnet", "--fallback-model", "haiku", "--effort", "low", "--max-turns", "1",
          "--system-prompt", "--setting-sources", "", "--tools", "", "--disallowedTools", DISALLOWED_TOOLS,
          "--disable-slash-commands", "--strict-mcp-config", "--permission-mode", "bypassPermissions",
          "--output-format", "stream-json", "--verbose", "--include-partial-messages"]


def test_t1_first_turn_args_every_flag(tmp_path):
    sid = str(uuid.uuid4())
    a = chat_args(spec("claude", tmp_path), sid, "new", "SYS")
    assert a[0] == "claude" and a[a.index("--session-id") + 1] == sid and uuid.UUID(sid).version == 4
    assert "--resume" not in a
    for f in FORBIDDEN_FLAGS:
        assert f not in a
    rest = [x for x in a[1:] if x not in ("--session-id", sid, "SYS")]
    assert rest == EXPECT  # CL2 의 인자를 낱낱이(순서까지)
    assert a[a.index("--system-prompt") + 1] == "SYS"


def test_t2_second_turn_only_head_differs(tmp_path):
    sid = str(uuid.uuid4())
    a = chat_args(spec("claude", tmp_path), sid, "new", "SYS")
    b = chat_args(spec("claude", tmp_path), sid, "resume", "SYS")
    assert "--session-id" not in b and b[b.index("--resume") + 1] == sid
    assert [x if x != "--session-id" else "--resume" for x in a] == b


async def test_t3_states_and_t5_env(fake_cli, tmp_path, monkeypatch):
    bin_, route, log = fake_cli
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-fixture")
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-fixture")
    (route / "answer.jsonl").write_text(stream("A1"), encoding="utf-8")
    llm = ChatCli(spec(bin_, tmp_path))
    conv = ChatConv("c1")
    sid0 = conv.session_id
    r = await llm.turn(conv, "Q1", prompt(), [])
    assert r["error"] is None and r["answer_raw"] == "A1" and conv.state == "open" and r["llm"]["mode"] == "new"
    calls = [x for x in read_log(log) if "-p" in x["argv"]]
    assert calls[-1]["stdin"] == "Q1" and calls[-1]["argv"][calls[-1]["argv"].index("--session-id") + 1] == sid0
    assert "ANTHROPIC_API_KEY" not in calls[-1]["env_keys"] and "TYPESAFE_API_KEY" not in calls[-1]["env_keys"]  # T5
    assert (tmp_path / "cwd").is_dir() and calls[-1]["cwd"].endswith("cwd")  # CL9 고정 cwd · 지우지 않음
    r2 = await llm.turn(conv, "Q2", prompt(), [])
    assert r2["llm"]["mode"] == "resume" and conv.session_id == sid0 and conv.state == "open"
    # open 에서 답 중 실패 → broken · 다음 턴을 띄우지 않는다
    monkeypatch.setenv("FAKE_CLI_DIE_AFTER", "2")
    r3 = await llm.turn(conv, "Q3", prompt(), [])
    assert r3["error"]["kind"] == "exit" and conv.state == "broken"
    n = len([x for x in read_log(log) if "-p" in x["argv"]])
    r4 = await llm.turn(conv, "Q4", prompt(), [])
    assert r4["error"]["kind"] == "broken" and len([x for x in read_log(log) if "-p" in x["argv"]]) == n


async def test_t3_new_failure_takes_new_uuid(fake_cli, tmp_path, monkeypatch):
    bin_, route, _ = fake_cli
    (route / "answer.jsonl").write_text(stream("A"), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLI_EXIT", "3")
    llm = ChatCli(spec(bin_, tmp_path))
    conv = ChatConv("c1")
    sid0 = conv.session_id
    r = await llm.turn(conv, "Q", prompt(), [])
    assert r["error"]["kind"] == "exit" and conv.state == "new" and conv.session_id != sid0
    assert r["session_state"] == "new"


async def test_t3_open_auth_failure_keeps_open(fake_cli, tmp_path, monkeypatch):
    bin_, route, _ = fake_cli
    (route / "answer.jsonl").write_text(stream("A"), encoding="utf-8")
    llm = ChatCli(spec(bin_, tmp_path))
    conv = ChatConv("c1", state="open")
    from clinic_chat.llm import auth

    monkeypatch.setenv("FAKE_CLI_AUTH", "api")
    auth._cache.clear()
    r = await llm.turn(conv, "Q", prompt(), [])
    assert r["error"]["kind"] == "auth" and conv.state == "open"  # CL10 ③


async def test_cl7_session_mismatch_and_tool_call(fake_cli, tmp_path, monkeypatch):
    bin_, route, _ = fake_cli
    (route / "answer.jsonl").write_text(stream("A"), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLI_SESSION_ID", str(uuid.uuid4()))
    llm = ChatCli(spec(bin_, tmp_path))
    conv = ChatConv("c1", state="open")
    r = await llm.turn(conv, "Q", prompt(), [])
    assert r["error"]["kind"] == "session_mismatch" and conv.state == "broken"
    monkeypatch.delenv("FAKE_CLI_SESSION_ID")
    (route / "answer.jsonl").write_text(stream("A", tools=[("Bash", {"c": "x"}, "out")]), encoding="utf-8")
    conv2 = ChatConv("c2", state="open")
    r2 = await llm.turn(conv2, "Q", prompt(), [])
    assert r2["error"]["kind"] == "tool_call" and conv2.state == "broken"


async def test_t4_conversation_lock_spawns_once(fake_cli, tmp_path, monkeypatch):
    bin_, route, log = fake_cli
    (route / "answer.jsonl").write_text(stream("A"), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLI_SLEEP", "0.5")
    llm = ChatCli(spec(bin_, tmp_path))
    conv = ChatConv("c1")
    a, b = await asyncio.gather(llm.turn(conv, "Q1", prompt(), []), llm.turn(conv, "Q2", prompt(), []))
    errs = sorted(str((x["error"] or {}).get("kind")) for x in (a, b))
    assert errs == ["None", "busy"]
    assert len([x for x in read_log(log) if "-p" in x["argv"]]) == 1


async def test_detail_has_no_home_path(fake_cli, tmp_path, monkeypatch):
    from pathlib import Path

    bin_, route, _ = fake_cli
    (route / "answer.jsonl").write_text(stream("A"), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLI_EXIT", "2")
    r = await ChatCli(spec(bin_, tmp_path)).turn(ChatConv("c"), "Q", prompt(), [])
    assert str(Path.home()) not in json.dumps(r)
