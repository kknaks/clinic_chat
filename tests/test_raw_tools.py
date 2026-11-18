"""WP-2 — A 열 원장 도구 3(합성 원장 · 가짜 JSON 2 파일)."""

from __future__ import annotations

import json

import pytest

from clinic_chat.raw_ledger.raw_tools import RawStore, RawToolError, paths_of


@pytest.fixture
def raw(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    (d / "fake-alpha.json").write_text(json.dumps({"items": [{"name": "가짜 시술 알파", "note": "x" * 50}] * 30}),
                                       encoding="utf-8")
    (d / "fake-beta.json").write_text(json.dumps({"map": {"키 하나": "가짜 값"}}), encoding="utf-8")
    (d / "readme.md").write_text("무시", encoding="utf-8")
    return d


def test_loads_all_json_and_manifest(raw):
    s = RawStore(raw)
    assert sorted(s.files) == ["fake-alpha", "fake-beta"]
    r = s.raw_list(depth=0)
    assert r["raw"]["files"] == 2 and len(r["raw"]["dir_manifest_sha16"]) == 16
    assert all("value" not in f for f in r["files"])


@pytest.mark.parametrize("args", [{"depth": 4}, {"depth": -1}])
def test_list_limits(raw, args):
    with pytest.raises(RawToolError):
        RawStore(raw).raw_list(**args)


def test_get_limits_and_truncation(raw):
    s = RawStore(raw)
    r = s.raw_get("fake-alpha.items", max_bytes=500)
    assert r["truncated"] is True and r["next_offset"] > 0
    with pytest.raises(RawToolError):
        s.raw_get("fake-alpha.items", max_bytes=20001)
    assert s.raw_get('fake-beta.map["키 하나"]')["value"] == "가짜 값"


@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/passwd", "nofile.x", "fake-alpha.missing"])
def test_outside_or_missing_path_is_tool_error(raw, path):
    with pytest.raises(RawToolError):
        RawStore(raw).raw_get(path)


def test_search_limits_and_norm(raw):
    s = RawStore(raw)
    r = s.raw_search("가짜시술", limit=5, snippet_chars=10)
    assert r["query_norm"] == "가짜시술" and r["truncated"] is True and len(r["hits"]) == 5
    for bad in ({"query": ""}, {"query": "x" * 101}, {"query": "a", "limit": 101}, {"query": "a", "snippet_chars": 201}):
        with pytest.raises(RawToolError):
            s.raw_search(**bad)


def test_start_failures(tmp_path):
    with pytest.raises(RawToolError):
        RawStore(tmp_path / "nope")
    (tmp_path / "empty").mkdir()
    with pytest.raises(RawToolError):
        RawStore(tmp_path / "empty")


def test_paths_of():
    assert paths_of("raw_get", {"path": "f.a"}, {}) == ["f.a"]
    assert paths_of("raw_search", {}, {"hits": [{"path": "f.b"}, {"path": "f.c"}]}) == ["f.b", "f.c"]
    assert paths_of("raw_list", {}, {"files": []}) == []


async def test_stdio_server_roundtrip(raw):
    """서버가 stdio 로 뜨고 raw_list 한 번 · 없는 경로는 tool 오류(서버는 죽지 않음)."""
    import os
    import sys

    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from clinic_chat.config import ROOT

    p = StdioServerParameters(command=sys.executable, args=["-m", "clinic_chat.raw_ledger.raw_mcp"],
                              env={**os.environ, "RAW_DIR": str(raw), "PYTHONPATH": str(ROOT)})
    async with stdio_client(p) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        assert names == {"raw_list", "raw_get", "raw_search"}
        bad = await s.call_tool("raw_get", {"path": "../x"})
        assert bad.is_error
        ok = await s.call_tool("raw_list", {"depth": 0})
        assert not ok.is_error
