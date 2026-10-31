"""실행 의존성 묶음 — 실물(env)과 모의(시험 · 모의 판)를 같은 꼴로 끼운다."""

from __future__ import annotations

import time
from collections.abc import Callable
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
    now: Callable[[], float] = time.perf_counter  # 시험은 가짜 시계
    app_commit: str | None = None
    # sections 입력을 받는 골드 도구 — 기동 때 허브 골드 MCP 스키마에서 셈(case3 이 Jev 「필요한 정보」를 붙이는 곳)
    section_tools: set[str] = field(default_factory=lambda: {"procedure_card"})
    # intent 입력을 받는 골드 도구(허브 SPEC-005 v3.1) — 기동 때 MCP 스키마에서 셈 · case3 가 Jev 의도 id 를 넘김
    intent_tools: set[str] = field(default_factory=set)
