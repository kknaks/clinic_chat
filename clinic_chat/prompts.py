"""프롬프트 파일 읽기 · 판(파일 sha256 앞 12자)."""

from __future__ import annotations

import hashlib
from functools import cache

from clinic_chat.config import ROOT

PROMPT_DIR = ROOT / "prompts"
NAMES = ("common", "case1", "case2", "case3", "judge", "extract")


@cache
def load(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def version(name: str) -> str:
    return hashlib.sha256((PROMPT_DIR / f"{name}.md").read_bytes()).hexdigest()[:12]


def versions() -> dict[str, str]:
    return {n: version(n) for n in NAMES}


def system_for(case: str) -> str:
    """공통부 + 케이스별 부분(R-5)."""
    return load("common").rstrip() + "\n\n" + load(case).rstrip() + "\n"
