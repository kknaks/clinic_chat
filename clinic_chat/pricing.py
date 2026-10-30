"""모델별 단가표(USD / 1M 토큰) — 질문당 예상 $ 계산용.

출처 · 날짜:
- Anthropic: claude-api 스킬 모델 표(캐시 2026-09-25) — Sonnet 5.5 $2 / $10 · Opus 5.5 $4 / $20 · 캐시 읽기 둘 다 $0.20.
  캐시 쓰기(5분 TTL)는 입력 단가의 1.25배(같은 스킬 prompt-caching 「Economics」).
- Jev(TypeSafe): jev-research §1(2026-10-30 공식 문서) — 입력 $0.042 / 1M · 출력 무료.
단가가 바뀌면 이 표만 고친다(기록에는 계산한 $ 와 이 표의 날짜를 같이 남김).
"""

from __future__ import annotations

PRICING_AS_OF = "2026-09-25(anthropic) · 2026-10-30(jev)"

PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
    "jev-1.14.0": {"input": 0.042, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0},
}


def cost_usd(model: str, usage: dict | None) -> float | None:
    """usage = {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens} → $ (단가 없으면 None)."""
    p = PRICES.get(model)
    if p is None or not usage:
        return None if p is None else 0.0
    t = (usage.get("input_tokens") or 0) * p["input"] + (usage.get("output_tokens") or 0) * p["output"] \
        + (usage.get("cache_creation_input_tokens") or 0) * p["cache_write"] \
        + (usage.get("cache_read_input_tokens") or 0) * p["cache_read"]
    return round(t / 1_000_000, 6)


def sum_usage(usages: list[dict]) -> dict:
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: sum((u or {}).get(k) or 0 for u in usages) for k in keys}
