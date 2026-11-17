"""세 열 공통 — 의존성 · 열 결과 꼴(SPEC-002 §7-2) · 인용 뽑기."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Any

from clinic_chat import prompts
from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.clients.jev_client import Jev, make_client
from clinic_chat.config import ROOT, Settings
from clinic_chat.llm.cli import CliSpec

GID_RE = re.compile(r"\bG[A-Z]{2}-\d{5}\b")
RAW_CITE_RE = re.compile(r"\[([A-Za-z0-9_\-]+(?:\.[^\s\[\]]+|\[(?:\d+|\"[^\"]*\")\])+)\]")


@dataclass
class Deps:
    settings: Settings
    hub: HubRest
    jev: Jev
    python: str = sys.executable
    root: str = str(ROOT)
    prompt_base: Any = None

    @classmethod
    def from_settings(cls, s: Settings) -> Deps:
        return cls(s, HubRest(s.hub_rest_url, page_limit=s.hub_page_limit), Jev(make_client(s)))

    def spec(self, reasoning: str, effort: str | None = None) -> CliSpec:
        """`effort` 를 주면 그 값으로 `--effort` 를 고정(속도 탭 — 모든 열 같은 값) · 없으면 설정 그대로."""
        s = self.settings
        return CliSpec(s.claude_cli_bin, s.answer_model, effort or s.claude_cli_effort, s.cli_timeout_sec,
                       reasoning=reasoning, lowest_effort=effort or s.lowest_effort,
                       mcp_output_tokens=s.mcp_output_tokens)

    def system(self, col: str) -> str:
        return prompts.read("common.md", self.prompt_base) + "\n\n" + prompts.read(f"col_{col}.md", self.prompt_base)


def cited_gids(answer: str) -> list[str]:
    return list(dict.fromkeys(GID_RE.findall(answer or "")))


def cited_raw(answer: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in RAW_CITE_RE.finditer(answer or "") if not GID_RE.fullmatch(m.group(1))))


def column(answer: str = "", *, end_state: str | None = None, timing: dict | None = None, tokens: Any = None,
           calls: dict | None = None, cited: list | None = None, error: Any = None, trace: Any = None) -> dict:
    return {"answer": answer, "end_state": end_state, "timing": timing or {}, "tokens": tokens or {},
            "calls": {"llm_round_trips": 0, "tool_calls": 0, "hub_calls": 0, "jev_calls": 0, **(calls or {})},
            "cost": {"billed": "subscription"}, "cited": cited or [], "error": error, "trace": trace}


def llm_tokens(r: dict) -> dict:
    """A · C 열 토큰 — B 와 같은 꼴 `{"llm": [왕복마다 usage]}`(도구 왕복 합 · stream-json usage). 왕복 usage 가 없으면
    CLI result 의 usage 한 줄."""
    rounds = r.get("token_rounds") or []
    return {"llm": rounds or ([r["tokens"]] if r.get("tokens") else [])}


def llm_input_bytes(question: str, calls: list[dict]) -> int:
    """A · C 열 LLM 입력 바이트 — 질문 + 도구 결과 바이트 합(지시문 제외 · 속도 탭 C 셈과 같음)."""
    return len((question or "").encode("utf-8")) + sum(int(c.get("output_bytes") or 0) for c in calls)


@dataclass
class ColumnOut:
    result: dict
    observed_model: str | None = None
    extra: dict = field(default_factory=dict)
