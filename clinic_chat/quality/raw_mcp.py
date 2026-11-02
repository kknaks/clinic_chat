"""채점용 원장 도구 3 을 감싼 로컬 stdio MCP 서버(cli-research §1-3 (가) · D-4 · SPEC-006 W-3).

CLI(claude -p)는 앱 안 Python 함수를 부를 수 없어 MCP 로 준다. 허브 원장 MCP 를 쓰지 않는다(채점이 실험 대상 서버에
기대지 않게). 같은 `raw_tools.RawStore` 를 쓰므로 근거 확인(check_evidence)과 기준이 하나다.

    RAW_DIR=… python -m clinic_chat.quality.raw_mcp
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from clinic_chat.quality.raw_tools import RawStore, RawToolError


def build(raw_dir: str) -> MCPServer:
    store = RawStore(raw_dir)
    m = MCPServer("clinic-chat-raw")

    def call(name: str, args: dict) -> dict[str, Any]:
        try:
            return store.call(name, {k: v for k, v in args.items() if v is not None or k == "file"})
        except RawToolError as e:
            raise ToolError(str(e)) from e

    @m.tool(name="raw_list", description="원장 파일 목록 · 키 구조 트리(값 본문 없음). file 없으면 21 파일 전부.")
    def raw_list(file: str | None = None, depth: int = 2) -> dict[str, Any]:
        return call("raw_list", {"file": file, "depth": depth})

    @m.tool(name="raw_get", description="경로의 원문 값. 경로 문법: 파일 이름 뒤 .키 · [번호] · [\"키\"]. 크면 잘리고 next_offset.")
    def raw_get(path: str, offset: int = 0, max_bytes: int | None = None) -> dict[str, Any]:
        return call("raw_get", {"path": path, "offset": offset, "max_bytes": max_bytes})

    @m.tool(name="raw_search", description="원장 문자열 값 · 키에서 부분 일치 검색(공백 · 대소문자만 무시).")
    def raw_search(query: str, files: list[str] | None = None, limit: int = 20,
                   snippet_chars: int = 80) -> dict[str, Any]:
        return call("raw_search", {"query": query, "files": files, "limit": limit, "snippet_chars": snippet_chars})

    return m


def main() -> None:
    build(os.environ["RAW_DIR"]).run("stdio")


if __name__ == "__main__":
    main()
