"""프롬프트 파일 읽기 · 판(파일 sha256 앞 12자)."""

from __future__ import annotations

import hashlib
import re
from functools import cache

from clinic_chat.config import ROOT

PROMPT_DIR = ROOT / "prompts"
NAMES = ("common", "case1", "case2", "case3", "judge", "extract")


@cache
def load(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


TOOL_SLOT = re.compile(r"\{tool:([a-z_]+)\}")
# 도구 이름 자리표 → provider 가 실제로 노출하는 이름(SPEC-006 §3-8 막는 법 1). claude-cli = MCP 서버 「hub」 접두 ·
# sdk = 앱이 준 이름 그대로 · codex-cli = 실물 미확인(S-3 전) → 짧은 이름 그대로(지금 글)
TOOL_PREFIX = {"claude-cli": "mcp__hub__", "codex-cli": "", "sdk": ""}


def fill(text: str, provider: str = "sdk") -> str:
    pre = TOOL_PREFIX.get(provider, "")
    return TOOL_SLOT.sub(lambda m: pre + m.group(1), text)


def version(name: str, provider: str | None = None) -> str:
    """판 = 파일 sha256 앞 12자 · 도구 이름 자리표가 있는 글(case2)은 provider 로 채운 뒤 글의 sha."""
    raw = (PROMPT_DIR / f"{name}.md").read_bytes()
    if provider is not None and TOOL_SLOT.search(raw.decode("utf-8")):
        raw = fill(raw.decode("utf-8"), provider).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def versions(provider: str | None = None) -> dict[str, str]:
    return {n: version(n, provider) for n in NAMES}


def system_for(case: str, provider: str = "sdk") -> str:
    """공통부 + 케이스별 부분(R-5) · 도구 이름 자리표는 provider 별로 채움."""
    return fill(load("common").rstrip() + "\n\n" + load(case).rstrip() + "\n", provider)
