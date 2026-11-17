"""Jev(TypeSafe System One) — `typesafe-sdk` `AsyncTypeSafeClient` · 모델 고정(SPEC-002 §6-4 · 옛 틀 통째).

요청 꼴은 SDK 그대로: `system_one(state=…, questions={이름: Choice(instructions, criteria) | Noul(instructions)})`.
Choice 답 = choice · confidence · probabilities / Noul 답 = noul(0~1) · 응답 머리 model · usage.
오류 · 시간 초과는 예외로 올린다 — 빈 값으로 바꾸지 않는다(SPEC-002 §6-4 「실행 중단」 재료).

실물 확인 E1(합성 state — 회사 데이터 없음):  uv run python -m clinic_chat.clients.jev_client --ping
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, RetryPolicy

from clinic_chat.config import Settings, load_settings

JEV_TIMEOUT_S = 10.0
CHOICE_MAX = 255  # criteria maxProperties


class JevError(RuntimeError):
    """Jev 호출 실패 — 그 질문의 B 열은 「실행 중단」."""


def make_client(settings: Settings, *, transport: Any = None, compression: Any = None,
                retry: RetryPolicy | None = None) -> AsyncTypeSafeClient | None:
    if not settings.has_typesafe_key:
        return None
    assert settings.typesafe_api_key is not None
    return AsyncTypeSafeClient(api_key=settings.typesafe_api_key.get_secret_value(),
                               model=settings.typesafe_default_model, timeout=JEV_TIMEOUT_S,
                               transport=transport, compression=compression, retry=retry)


def choice_stats(probabilities: dict[str, float]) -> tuple[float, float | None]:
    """p_max · 1·2위 비(p_max / p_second)."""
    ps = sorted(probabilities.values(), reverse=True)
    p_max = ps[0] if ps else 0.0
    ratio = (ps[0] / ps[1]) if len(ps) > 1 and ps[1] > 0 else None
    return p_max, (round(ratio, 4) if ratio is not None else None)


@dataclass
class JevAnswer:
    model: str | None
    choices: dict[str, dict] = field(default_factory=dict)  # 이름 → {choice, confidence, probabilities}
    nouls: dict[str, float] = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    ms: float = 0.0


class Jev:
    """앱이 쓰는 얇은 감쌈 — 요청 수 · 실제 model id 를 모은다. 질문 꼴은 dict(`{"type": "choice", …}`)도 받는다."""

    def __init__(self, client: AsyncTypeSafeClient | None) -> None:
        self.client = client
        self.calls = 0
        self.ms_total = 0.0  # 응답 받은 요청의 ms 합(속도 탭 단계 막대 — WORK-004)
        self.models: set[str] = set()

    async def ask(self, state: Any, questions: dict[str, Any]) -> JevAnswer:
        if self.client is None:
            raise JevError("TYPESAFE_API_KEY 없음")
        if not questions:
            raise JevError("빈 질문")
        for name, q in questions.items():
            crit = q.get("criteria") if isinstance(q, dict) else getattr(q, "criteria", None)
            if isinstance(crit, dict) and len(crit) > CHOICE_MAX:
                raise JevError(f"Choice {name} 옵션 {len(crit)} > {CHOICE_MAX}")
        t = time.perf_counter()
        self.calls += 1
        try:
            res = await self.client.system_one(state=state, questions=questions)
        except Exception as e:  # noqa: BLE001 — SDK 오류 · 시간 초과 전부 「실행 중단」 재료로 올린다
            raise JevError(f"{type(e).__name__}: {str(e)[:300]}") from e
        ms = round((time.perf_counter() - t) * 1000, 1)
        self.ms_total += ms
        out = JevAnswer(model=getattr(res, "model", None), ms=ms)
        if out.model:
            self.models.add(out.model)
        for name, a in (res.choices or {}).items():
            out.choices[name] = {"choice": a.choice, "confidence": a.confidence,
                                 "probabilities": dict(a.probabilities)}
        for name, a in (res.nouls or {}).items():
            out.nouls[name] = float(a.noul)
        u = getattr(res, "usage", None)
        if u is not None:
            out.usage = u.model_dump() if hasattr(u, "model_dump") else dict(u)
        missing = [n for n in questions if n not in out.choices and n not in out.nouls]
        if missing:
            raise JevError(f"응답에 없는 질문: {missing[:5]}")
        return out


def noul(instructions: str) -> Noul:
    return Noul(instructions=instructions)


def choice(instructions: str, options: dict[str, str]) -> Choice:
    return Choice(instructions=instructions, criteria=options)


async def _ping(settings: Settings) -> dict:
    """E1 — 합성 state · Choice 1 + Noul 2 를 한 요청으로(회사 데이터 없음)."""
    jev = Jev(make_client(settings))
    state = {"question": "Which fruit is yellow and curved?",
             "candidates": [{"id": "c0", "name": "banana"}, {"id": "c1", "name": "brick"}]}
    qs = {
        "kind": choice("Which option best matches what `question` asks about?",
                       {"fruit": "a kind of fruit", "tool": "a kind of tool", "none": "neither"}),
        "candidate_0": noul("Does `candidates[0]` answer `question`?"),
        "candidate_1": noul("Does `candidates[1]` answer `question`?"),
    }
    a = await jev.ask(state, qs)
    c = a.choices["kind"]
    return {"ok": True, "model": a.model, "choice": c["choice"], "confidence": c["confidence"],
            "probabilities_keys": len(c["probabilities"]), "noul": a.nouls, "ms": a.ms,
            "request_shape": "system_one(state, {choice ×1, noul ×2})"}


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.clients.jev_client")
    ap.add_argument("--ping", action="store_true", help="E1 — 합성 state 로 Jev 1회")
    args = ap.parse_args()
    if not args.ping:
        ap.print_help()
        return
    s = load_settings()
    if not s.has_typesafe_key:
        print(json.dumps({"ok": False, "error": "TYPESAFE_API_KEY 없음 — 못 돌림"}, ensure_ascii=False))
        raise SystemExit(2)
    try:
        print(json.dumps(asyncio.run(_ping(s)), ensure_ascii=False))
    except JevError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
