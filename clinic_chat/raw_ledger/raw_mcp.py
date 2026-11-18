"""A 열 원장 도구 3 을 감싼 로컬 stdio MCP 서버(SPEC-002 §2-3 · 옛 틀 `quality/raw_mcp.py` 꼴).

CLI(`claude -p`)는 앱 안 함수를 부를 수 없어 MCP 로 준다. 서버 이름 = `clinic-chat-raw`(O-11).
`RAW_DIR` 이 없거나 JSON 이 0 이면 시작 실패 — 그 열 오류. 도메인 오류는 같은 문구의 tool 오류(서버는 죽지 않는다).

    RAW_DIR=… python -m clinic_chat.raw_ledger.raw_mcp
    RAW_DIR=… python -m clinic_chat.raw_ledger.raw_mcp --check     # 실물 확인 — 파일 수 · manifest sha16 만
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from clinic_chat.raw_ledger.raw_tools import RawStore, RawToolError

SERVER_NAME = "clinic-chat-raw"
TOOLS = ("raw_list", "raw_get", "raw_search")


def build(raw_dir: str) -> MCPServer:
    store = RawStore(raw_dir)
    m = MCPServer(SERVER_NAME)

    def call(name: str, args: dict) -> dict[str, Any]:
        try:
            return store.call(name, {k: v for k, v in args.items() if v is not None})
        except RawToolError as e:
            raise ToolError(str(e)) from e
        except TypeError as e:
            raise ToolError(f"입력 오류: {e}") from e

    @m.tool(name="raw_list", description="원장 파일 목록 · 키 구조 트리(값 본문 없음). file 없으면 전 파일. depth 0~3(기본 2).")
    def raw_list(file: str | None = None, depth: int = 2) -> dict[str, Any]:
        return call("raw_list", {"file": file, "depth": depth})

    @m.tool(name="raw_get",
            description="경로의 원문 값. 경로 문법: 파일 이름 뒤 .키 · [번호] · [\"키\"]. max_bytes 최대 20000 · 크면 잘리고 next_offset.")
    def raw_get(path: str, offset: int = 0, max_bytes: int | None = None) -> dict[str, Any]:
        return call("raw_get", {"path": path, "offset": offset, "max_bytes": max_bytes})

    @m.tool(name="raw_search",
            description="원장 문자열 값 · 키에서 부분 일치 검색(공백 · 대소문자만 무시). query 1~100자 · limit 1~100(기본 20) · snippet_chars 1~200(기본 80).")
    def raw_search(query: str, files: list[str] | None = None, limit: int = 20,
                   snippet_chars: int = 80) -> dict[str, Any]:
        return call("raw_search", {"query": query, "files": files, "limit": limit, "snippet_chars": snippet_chars})

    return m


def main() -> None:
    raw_dir = os.environ.get("RAW_DIR", "")
    if "--check" in sys.argv[1:]:
        store = RawStore(raw_dir)
        r = store.raw_list(depth=0)
        print(json.dumps({"ok": True, "files": r["raw"]["files"], "dir_manifest_sha16": r["raw"]["dir_manifest_sha16"]}))
        return
    build(raw_dir).run("stdio")


if __name__ == "__main__":
    main()
