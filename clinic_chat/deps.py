"""실행 의존성 묶음 — 실물(env)과 모의(시험 · 모의 판)를 같은 꼴로 끼운다."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clinic_chat.clients.hub_mcp import HubMCP
from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.config import Settings

NO_KEY = "키 없음"


@dataclass
class Deps:
    settings: Settings
    anthropic: Any  # AsyncAnthropic 또는 모의 · 키 없으면 None
    jev: Any  # AsyncTypeSafeClient 또는 모의 · 키 없으면 None
    gold_mcp: HubMCP
    raw_mcp: HubMCP
    hub_rest: HubRest
    raw_store: Any = None  # quality.raw_tools.RawStore(RAW_DIR 없으면 None)
    normalizer: Any = None  # quality.normalize(SEMANTIC_JSON 없으면 None)
    semantic: Any = None  # SEMANTIC_JSON dictionary(expressions)
    vocab: dict | None = None  # T9 캐시(시작 때 1회)
    startup: dict = field(default_factory=dict)
