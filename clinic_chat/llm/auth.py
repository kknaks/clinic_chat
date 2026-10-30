"""CLI 인증 확인 — 구독(Claude: claude.ai OAuth · Codex: ChatGPT 로그인)이 아니면 그 열은 오류(조용히 API 과금 금지)."""

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


def parse_codex_status(text: str) -> AuthCheck:
    t = text.strip()
    if "ChatGPT" in t:
        return AuthCheck(True, "subscription", "ChatGPT 로그인")
    if "API key" in t or "api key" in t.lower():
        return AuthCheck(False, "api_key", "구독 아님(API 키 로그인)")
    if "Not logged in" in t or "not logged in" in t.lower():
        return AuthCheck(False, "not_logged_in", "Codex CLI 로그인 안 됨")
    return AuthCheck(False, "unknown", "codex login status 를 읽지 못함")


async def check(provider: str, bin_path: str, *, force: bool = False) -> AuthCheck:
    now = time.monotonic()
    hit = _cache.get(provider)
    if hit and not force and now - hit[0] < CACHE_S:
        return hit[1]
    try:
        if provider == "claude-cli":
            _, out = await _run([bin_path, "auth", "status", "--json"])
            res = parse_claude_status(out)
            _, ver = await _run([bin_path, "--version"])
        else:
            _, out = await _run([bin_path, "login", "status"])
            res = parse_codex_status(out)
            _, ver = await _run([bin_path, "--version"])
        res.version = ver.strip().splitlines()[0] if ver.strip() else None
    except FileNotFoundError:
        res = AuthCheck(False, "unknown", f"{bin_path} 없음")
    _cache[provider] = (now, res)
    return res
