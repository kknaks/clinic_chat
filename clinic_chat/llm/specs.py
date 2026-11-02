"""provider → CLI 실행 설정(답 · 이름 뽑기 · 채점)."""

from __future__ import annotations

from clinic_chat.config import Settings
from clinic_chat.llm.cli import CliSpec


def answer_spec(s: Settings, provider: str) -> CliSpec:
    if provider == "claude-cli":
        return CliSpec(provider, s.claude_cli_bin, s.claude_cli_model, s.claude_cli_effort, s.cli_timeout_sec,
                       max_budget_usd=s.cli_max_budget_usd, result_max=s.mcp_result_max_chars)
    if provider == "codex-cli":
        return CliSpec(provider, s.codex_cli_bin, s.codex_cli_model, s.codex_cli_effort, s.cli_timeout_sec,
                       service_tier=s.codex_service_tier or None, result_max=s.mcp_result_max_chars)
    raise ValueError(f"CLI provider 아님: {provider}")


def judge_spec(s: Settings, role: str) -> CliSpec:
    """채점(① 이름 뽑기 · ② 채점) — Claude CLI Opus(10-30 사용자 결정)."""
    if s.judge_provider != "claude-cli":
        raise ValueError(f"채점 provider 는 claude-cli 만: {s.judge_provider}")
    model = s.extract_model if role == "extract" else s.judge_model
    effort = s.extract_cli_effort if role == "extract" else s.judge_cli_effort
    return CliSpec("claude-cli", s.claude_cli_bin, model, effort, s.cli_timeout_sec, max_budget_usd=s.judge_max_budget_usd,
                   result_max=s.mcp_result_max_chars)


def describe(s: Settings, provider: str) -> dict:
    """화면 · 기록용 — provider · 모델 · effort."""
    if provider == "sdk":
        return {"provider": "sdk", "model": s.answer_model, "effort": "high", "billed": "api"}
    sp = answer_spec(s, provider)
    return {"provider": provider, "model": sp.model, "effort": sp.effort, "billed": "subscription",
            "service_tier": sp.service_tier}
