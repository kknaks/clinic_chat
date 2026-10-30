"""Jev(TypeSafe System One) — `typesafe-sdk` `AsyncTypeSafeClient` · 모델 고정(SPEC-006 §2-1).

요청 꼴은 SDK 그대로: `system_one(state=…, questions={이름: Choice(instructions, criteria) | Noul(instructions)})`.
Choice 답 = choice · confidence · probabilities / Noul 답 = noul(0~1) · 응답 머리 model · usage.
"""

from __future__ import annotations

from typing import Any

from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from clinic_chat.config import Settings

JEV_TIMEOUT_S = 10.0
CHOICE_MAX = 255  # criteria maxProperties


def make_client(settings: Settings, *, transport: Any = None, compression: Any = None,
                retry: RetryPolicy | None = None) -> AsyncTypeSafeClient | None:
    if not settings.has_typesafe_key:
        return None
    return AsyncTypeSafeClient(api_key=settings.typesafe_api_key.get_secret_value(),
                               model=settings.typesafe_default_model, timeout=JEV_TIMEOUT_S,
                               transport=transport, compression=compression, retry=retry)


def choice_stats(probabilities: dict[str, float]) -> tuple[float, float | None]:
    """p_max · 1·2위 비(p_max / p_second)."""
    ps = sorted(probabilities.values(), reverse=True)
    p_max = ps[0] if ps else 0.0
    ratio = (ps[0] / ps[1]) if len(ps) > 1 and ps[1] > 0 else None
    return p_max, (round(ratio, 4) if ratio is not None else None)
