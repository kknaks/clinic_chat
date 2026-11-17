"""WP-2 · WP-4 — A 열(stdio 원장 MCP) · C 열(http · 도구 8) 인자 · trace 꼴(모의 CLI)."""

from __future__ import annotations

import json

from clinic_chat.columns import col_a, col_c
from clinic_chat.columns.base import Deps
from tests.conftest import make_settings, read_log, stream
from tests.fake_hub import FakeHub
from tests.fake_jev import FakeJev


def deps(tmp_path, bin_, **kw):
    s = make_settings(tmp_path, claude_cli_bin=bin_, **kw)
    return Deps(s, FakeHub().client(), FakeJev().jev(s))


async def test_col_a_stdio_and_trace(tmp_path, fake_cli):
    bin_, route, log = fake_cli
    search = json.dumps({"total": 30, "truncated": True, "hits": [{"path": "fake-alpha.items[0].name"}]})
    get = json.dumps({"path": "fake-alpha.items[0]", "truncated": False, "value": {}})
    (route / "tools.jsonl").write_text(stream("답 [fake-alpha.items[0].name]", mcp="clinic-chat-raw", tools=[
        ("mcp__clinic-chat-raw__raw_search", {"query": "가짜"}, search),
        ("mcp__clinic-chat-raw__raw_get", {"path": "fake-alpha.items[0]"}, get)]), encoding="utf-8")
    out = await col_a.run("q", deps(tmp_path, bin_, raw_dir=str(tmp_path)))
    a = [x for x in read_log(log) if "-p" in x["argv"]][0]["argv"]
    cfg = json.loads(a[a.index("--mcp-config") + 1])["mcpServers"]["clinic-chat-raw"]
    assert cfg["type"] == "stdio" and cfg["args"] == ["-m", "clinic_chat.raw_ledger.raw_mcp"]
    assert a[a.index("--allowedTools") + 1] == "mcp__clinic-chat-raw"
    tc = out.result["trace"]["tool_calls"]
    assert [set(x) >= {"tool", "input", "output_bytes", "paths", "truncated"} for x in tc] == [True, True]
    assert tc[0]["paths"] == ["fake-alpha.items[0].name"] and tc[0]["truncated"] is True
    assert tc[1]["paths"] == ["fake-alpha.items[0]"]
    assert out.result["cited"] == ["fake-alpha.items[0].name"] and out.result["end_state"] is None


async def test_col_c_http_seven_tools_and_edge_ids(tmp_path, fake_cli):
    bin_, route, log = fake_cli
    nb = json.dumps({"items": [{"edge_id": "GED-90001", "gold_table": "g_pair", "gold_id": "GPR-90009"}]})
    fc = json.dumps({"rows": [{"gold_table": "g_profile", "gid": "GPF-90001"}],
                     "groups": [{"rows": [{"gold_table": "g_indication", "gid": "GIN-90001"}]}],
                     "unreached": [{"gold_table": "g_indication", "gid": "GIN-90002"}]})
    (route / "tools.jsonl").write_text(stream("답 [GPF-90001]", mcp="hub", tools=[
        ("mcp__hub__neighbors", {"node_id": "procedure:Z"}, nb), ("mcp__hub__facets", {"node": "procedure:Z"}, fc)]),
        encoding="utf-8")
    out = await col_c.run("q", deps(tmp_path, bin_))
    a = [x for x in read_log(log) if "-p" in x["argv"]][0]["argv"]
    cfg = json.loads(a[a.index("--mcp-config") + 1])["mcpServers"]["hub"]
    assert cfg["type"] == "http"
    allowed = a[a.index("--allowedTools") + 1].split(",")
    assert len(allowed) == 8 and "mcp__hub__health" not in allowed and "mcp__hub__coverage" in allowed
    for f in ("--tools", "--setting-sources", "--strict-mcp-config", "--no-session-persistence"):
        assert f in a
    tc = out.result["trace"]["tool_calls"]
    assert tc[0]["gids"] == [] and tc[0]["edge_gold_ids"] == ["GPR-90009"]
    assert tc[1]["gids"] == ["GPF-90001", "GIN-90001", "GIN-90002"]
