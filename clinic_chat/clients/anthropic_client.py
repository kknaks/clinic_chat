"""Anthropic 클라이언트 — 세 케이스 같은 Sonnet 설정(SPEC-006 §3-1) · 스트림 한 바퀴."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from anthropic import AsyncAnthropic

from clinic_chat.config import Settings

# 세 케이스 같은 조건 — thinking 끔(= between_tools) · effort high · tool_choice auto · 폴백 없음
ANSWER_THINKING = {"type": "between_tools"}
ANSWER_EFFORT = "high"
ANSWER_MAX_TOKENS = 8000


def make_client(settings: Settings) -> AsyncAnthropic | None:
    if not settings.has_anthropic_key:
        return None
    return AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value(), max_retries=2)


CACHE = {"type": "ephemeral"}  # 5분 TTL


def cached_system(system: str) -> list[dict]:
    """시스템 프롬프트 끝에 캐시 표시 — 도구 설명(tools)이 system 앞에 그려지므로 둘을 같이 캐시한다."""
    return [{"type": "text", "text": system, "cache_control": CACHE}]


def answer_params(model: str, system: str, messages: list, tools: list | None, tool_choice: dict | None = None,
                  *, loop_cache: bool = False) -> dict:
    """세 케이스 같은 Sonnet 설정. 캐시: 시스템(+도구) 표시는 세 케이스 같음 · loop_cache=True(도구 루프)는
    마지막 블록 자동 표시를 더해 바퀴마다 앞 대화를 다시 읽기(캐시 읽기)로 받는다."""
    p: dict[str, Any] = {
        "model": model,
        "max_tokens": ANSWER_MAX_TOKENS,
        "system": cached_system(system),
        "messages": messages,
        "thinking": ANSWER_THINKING,
        "output_config": {"effort": ANSWER_EFFORT},
    }
    if tools:
        p["tools"] = tools
        p["tool_choice"] = tool_choice or {"type": "auto"}
    if loop_cache:
        p["cache_control"] = CACHE
    return p


def usage_dict(u: Any) -> dict:
    if u is None:
        return {}
    return {k: getattr(u, k, None) or 0 for k in
            ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}


async def stream_once(client: Any, params: dict, on_text: Callable[[str], None]) -> Any:
    """`messages.stream` 한 번 — 텍스트 델타(thinking 제외)마다 on_text · 마지막 Message 를 돌려줌."""
    async with client.messages.stream(**params) as s:
        async for ev in s:
            if ev.type == "text" and ev.text:
                on_text(ev.text)
        return await s.get_final_message()


def block_to_param(b: Any) -> dict:
    """응답 블록을 다음 요청 assistant 내용으로(thinking 블록 포함 그대로)."""
    d = b.model_dump(exclude_none=True)
    d.pop("parsed_output", None)
    if d.get("type") == "text":
        d.pop("citations", None)
    return d
