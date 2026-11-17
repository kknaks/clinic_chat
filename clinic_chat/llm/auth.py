"""CLI 인증 확인 — Claude 구독(claude.ai OAuth)이 아니면 그 열은 오류(조용히 API 과금 금지 · 옛 틀 Claude 갈래만)."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

from clinic_chat.llm.cli_process import child_env

CACHE_S = 600


@dataclass
class AuthCheck:
    ok: bool
    kind: str  # subscription | api_key | not_logged_in | unknown
    detail: str
    version: str | None = None


_cache: dict[str, tuple[float, AuthCheck]] = {}


async def _run(cmd: list[str], timeout: float = 20) -> tuple[int | None, str]:
    p = await asyncio.create_subprocess_exec(*cmd, env=child_env(), stdin=asyncio.subprocess.DEVNULL,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except TimeoutError:
        p.kill()
        return None, "시간 초과"
    return p.returncode, out.decode("utf-8", errors="replace")


def parse_claude_status(text: str) -> AuthCheck:
    try:
        d = json.loads(text)
    except ValueError:
        return AuthCheck(False, "unknown", "claude auth status 를 읽지 못함")
    if not d.get("loggedIn"):
        return AuthCheck(False, "not_logged_in", "Claude CLI 로그인 안 됨")
    method, provider = d.get("authMethod"), d.get("apiProvider")
    if method == "claude.ai" and provider in (None, "firstParty"):
        return AuthCheck(True, "subscription", f"claude.ai · {d.get('subscriptionType') or '구독'}")
    return AuthCheck(False, "api_key", f"구독 아님(authMethod={method} · apiProvider={provider})")


async def check(bin_path: str, *, force: bool = False) -> AuthCheck:
    now = time.monotonic()
    hit = _cache.get(bin_path)
    if hit and not force and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        _, out = await _run([bin_path, "auth", "status", "--json"])
        res = parse_claude_status(out)
        _, ver = await _run([bin_path, "--version"])
        res.version = ver.strip().splitlines()[0] if ver.strip() else None
    except (FileNotFoundError, PermissionError):
        res = AuthCheck(False, "unknown", f"{bin_path} 없음")
    _cache[bin_path] = (now, res)
    return res
