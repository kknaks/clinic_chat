"""모의 `claude` — 시험 전용 · 실물 호출 없음(옛 틀 tests/fake_cli 꼴).

FAKE_CLI_LOG: argv · stdin · 자식 env 키 이름을 JSON 한 줄로 덧붙임 / FAKE_CLI_ROUTE: 폴더 — 호출 꼴마다 파일
(judge.jsonl = --json-schema · tools.jsonl = --mcp-config · answer.jsonl = 그 밖) / FAKE_CLI_AUTH=api 면 구독 아님 /
FAKE_CLI_EXIT: 종료 코드.
"""

import json
import os
import sys
from pathlib import Path

kind, argv = sys.argv[1], sys.argv[2:]
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
log = os.environ.get("FAKE_CLI_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "stdin": stdin, "env_keys": sorted(os.environ), "cwd": os.getcwd(),
                            "env_thinking": os.environ.get("MAX_THINKING_TOKENS")}, ensure_ascii=False) + "\n")
if argv[:1] == ["--version"]:
    print("2.1.322 (Claude Code)")
    sys.exit(0)
if argv[:2] == ["auth", "status"]:
    sub = os.environ.get("FAKE_CLI_AUTH", "") != "api"
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai" if sub else "apiKey", "apiProvider": "firstParty"}))
    sys.exit(0)
route = os.environ.get("FAKE_CLI_ROUTE")
# 상담(SPEC-004) 가지 — 더하기만: `--session-id` · `--resume` 값을 init 줄의 session_id 로 되돌림(FAKE_CLI_SESSION_ID 면 그 값) ·
# FAKE_CLI_DIE_AFTER=N 줄 뒤 종료 코드 1 · FAKE_CLI_SLEEP 초 뒤 시작 · 지금 시험이 쓰는 동작은 그대로
sid = None
for flag in ("--session-id", "--resume"):
    if flag in argv:
        sid = argv[argv.index(flag) + 1]
sid = os.environ.get("FAKE_CLI_SESSION_ID") or sid
if os.environ.get("FAKE_CLI_SLEEP"):
    import time

    time.sleep(float(os.environ["FAKE_CLI_SLEEP"]))
die = int(os.environ["FAKE_CLI_DIE_AFTER"]) if os.environ.get("FAKE_CLI_DIE_AFTER") else None
if route:
    name = "judge.jsonl" if "--json-schema" in argv else ("tools.jsonl" if "--mcp-config" in argv else "answer.jsonl")
    p = Path(route) / name
    if p.is_file():
        k = 0
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                if sid is not None and '"init"' in ln:
                    ev = json.loads(ln)
                    if ev.get("subtype") == "init":
                        ev["session_id"] = sid
                        ln = json.dumps(ev, ensure_ascii=False)
                if die is not None and k >= die:
                    sys.exit(1)
                print(ln, flush=True)
                k += 1
sys.exit(int(os.environ.get("FAKE_CLI_EXIT", "0")))
