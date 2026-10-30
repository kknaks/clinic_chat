"""모의 CLI — `claude` · `codex` 대신 띄우는 실행 파일(시험 전용 · 실물 호출 없음).

FAKE_CLI_LOG: argv · 자식 env 의 키 이름을 JSON 한 줄로 덧붙임 / FAKE_CLI_LINES: 내보낼 JSONL 파일 /
FAKE_CLI_AUTH: claude=subscription|api · codex=chatgpt|apikey / FAKE_CLI_OUT: codex -o 파일에 쓸 내용 /
FAKE_CLI_SLEEP: 줄 사이 초 / FAKE_CLI_EXIT: 종료 코드.
"""

import json
import os
import sys
import time
from pathlib import Path

kind, argv = sys.argv[1], sys.argv[2:]  # 래퍼가 claude · codex 를 첫 인자로
log = os.environ.get("FAKE_CLI_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, "argv": argv, "env_keys": sorted(os.environ), "cwd": os.getcwd(),
                            "codex_home": os.environ.get("CODEX_HOME")}, ensure_ascii=False) + "\n")
auth = os.environ.get("FAKE_CLI_AUTH", "")
if argv[:1] == ["--version"]:
    print("2.1.284 (Claude Code)" if kind == "claude" else "codex-cli 0.166.0")
    sys.exit(0)
if kind == "claude" and argv[:2] == ["auth", "status"]:
    sub = auth != "api"
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai" if sub else "apiKey",
                      "apiProvider": "firstParty", "subscriptionType": "max" if sub else None}))
    sys.exit(0)
if kind == "codex" and argv[:2] == ["login", "status"]:
    print("Logged in using an API key - sk-…" if auth == "apikey" else "Logged in using ChatGPT")
    sys.exit(0)
lines = os.environ.get("FAKE_CLI_LINES")
route = os.environ.get("FAKE_CLI_ROUTE")  # 폴더 — 호출 꼴마다 다른 파일(runner 시험)
if route:
    if kind == "codex":
        name = "codex.jsonl"
    elif "--json-schema" in argv:
        name = "extract.jsonl" if '"names"' in argv[argv.index("--json-schema") + 1] else "judge.jsonl"
    elif "--mcp-config" in argv:
        name = "tools.jsonl"
    else:
        name = "answer.jsonl"
    lines = str(Path(route) / name)
gap = float(os.environ.get("FAKE_CLI_SLEEP", "0.01"))
if lines:
    for ln in Path(lines).read_text(encoding="utf-8").splitlines():
        if ln.strip():
            print(ln, flush=True)
            time.sleep(gap)
out = os.environ.get("FAKE_CLI_OUT")
if out and "-o" in argv:
    Path(argv[argv.index("-o") + 1]).write_text(out, encoding="utf-8")
sys.exit(int(os.environ.get("FAKE_CLI_EXIT", "0")))
