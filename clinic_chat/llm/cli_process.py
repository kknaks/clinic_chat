"""CLI 서브프로세스 실행 — 자기 프로세스 그룹 · stdout 줄마다 콜백 · 타임아웃 · 끝나면 그룹째 정리(MCP 자식까지).

자식 env 는 늘 `child_env()` 로 만든다: API 키 꼴 env 를 지워 CLI 가 구독 인증만 쓰게 한다(cli-research B-1 —
`claude -p` 는 env 에 ANTHROPIC_API_KEY 가 있으면 구독 대신 키로 과금 · Codex 는 CODEX_API_KEY · OPENAI_API_KEY).
참고 설계: Strong_hajin platform/cli_process.py `subprocess_runner`(동기) — 여기선 asyncio 로.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass, field

# 자식에게 넘기지 않는 env — 키 · API 경로 전환 · 앱 비밀값
SCRUB_ENV = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "TYPESAFE_API_KEY",
)


def child_env(extra: dict[str, str] | None = None, base: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in (os.environ if base is None else base).items() if k not in SCRUB_ENV}
    env.update(extra or {})
    for k in SCRUB_ENV:  # extra 로도 못 넣음
        env.pop(k, None)
    return env


@dataclass
class ProcResult:
    returncode: int | None
    stderr: str
    timed_out: bool = False
    killed: bool = False
    lines: int = 0
    stderr_tail: list[str] = field(default_factory=list)


class StopRun(Exception):
    """줄 콜백이 실행을 멈추라고 할 때(예: 구독이 아닌 인증이 보임)."""


async def run_cli(cmd: list[str], *, cwd: str, env: dict[str, str], timeout_s: float,
                  on_line: Callable[[str], None]) -> ProcResult:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=16 * 1024 * 1024)
    res = ProcResult(returncode=None, stderr="")
    err_chunks: list[str] = []

    async def read_out() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            res.lines += 1
            on_line(raw.decode("utf-8", errors="replace"))

    async def read_err() -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            err_chunks.append(raw.decode("utf-8", errors="replace"))

    def kill_group() -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGTERM)

    try:
        await asyncio.wait_for(asyncio.gather(read_out(), read_err(), proc.wait()), timeout=timeout_s)
    except TimeoutError:
        res.timed_out = True
        kill_group()
    except StopRun:
        res.killed = True
        kill_group()
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        kill_group()  # 남은 MCP 자식까지
    res.returncode = proc.returncode
    res.stderr = "".join(err_chunks)[-4000:]
    res.stderr_tail = res.stderr.splitlines()[-5:]
    return res
