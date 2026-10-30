"""모의 Jev — 실물 `AsyncTypeSafeClient` 에 `httpx2.MockTransport` 를 끼워 SDK 요청 · 응답 꼴을 그대로 지난다."""

from __future__ import annotations

import json
from typing import Any

import httpx2
from typesafe_sdk import RetryPolicy

from clinic_chat.clients.jev_client import make_client

NONE = "해당 없음"
# 「필요한 정보」 모의 판단 — 질문 낱말 → 카드 칸
INFO_RULES = {
    "checks": r"임신|체질|질환|복용|해도 돼", "safety.side_effects": r"부작용|멍|붓기", "safety.complications": r"부작용|합병증|위험",
    "recovery": r"회복|출근|일상|다운타임|얼마나 가", "aftercare": r"회복|사우나|관리|세안|운동", "method": r"어떻게|방법|원리",
    "overview": r"뭐예요|무엇|어떤 시술인", "dose": r"몇 ?cc|용량|회차|몇 번", "products": r"제품|브랜드", "ops": r"마취|아파|통증|시간 걸",
}


class FakeJev:
    def __init__(self, intent: Any = None, choices: dict[str, str] | None = None,
                 flat: set[str] | None = None, status: int | None = None, timeout: bool = False) -> None:
        self.intent = intent
        self.choices = choices or {}
        self.flat = flat or set()  # 확률을 고르게(낮은 신뢰)
        self.status = status
        self.timeout = timeout
        self.requests: list[dict] = []
        self.estimate_tokens = None  # 비용 추정 때 요청 크기 → 입력 토큰 어림

    def client(self, settings):
        return make_client(settings, transport=httpx2.MockTransport(self.handle), compression=False,
                           retry=RetryPolicy(max_retries=0))

    def _pick(self, name: str, q: dict, state: str) -> str:
        crit: dict[str, Any] = q["criteria"]
        if name == "intent" and self.intent:
            return self.intent(state) if callable(self.intent) else self.intent
        if name in self.choices:
            return self.choices[name]
        for k, d in crit.items():
            if k != NONE and isinstance(d, str) and d.split(" · ")[0] in state:
                return k
        return NONE if NONE in crit else next(iter(crit))

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.timeout:
            raise httpx2.ReadTimeout("fixture timeout", request=request)
        body = json.loads(request.content)
        self.requests.append(body)
        if self.status:
            return httpx2.Response(self.status, json={"error": {"type": "fixture", "message": "fixture error"}})
        state = body["state"] if isinstance(body["state"], str) else json.dumps(body["state"], ensure_ascii=False)
        answers: dict[str, Any] = {}
        picked: dict[str, str] = {}
        for name, q in body["questions"].items():
            if q["type"] == "choice":
                keys = list(q["criteria"])
                c = self._pick(name, q, state)
                n = len(keys)
                if name in self.flat:
                    probs = {k: 1 / n for k in keys}
                    probs[c] += 0.01
                else:
                    probs = {k: (0.3 / (n - 1) if n > 1 else 0) for k in keys}
                    probs[c] = 0.7 if n > 1 else 1.0
                s = sum(probs.values())
                probs = {k: v / s for k, v in probs.items()}
                pm = probs[c]
                conf = (pm - 1 / n) / (1 - 1 / n) if n > 1 else 1.0
                answers[name] = {"type": "choice", "choice": c, "confidence": round(max(conf, 0), 4),
                                 "probabilities": probs}
                picked[name] = c
        for name, q in body["questions"].items():
            if q["type"] == "noul":
                if name.startswith("info:"):
                    import re

                    pat = INFO_RULES.get(name[5:])
                    v = 0.9 if pat and re.search(pat, state) else 0.1
                elif name.startswith("said:"):
                    v = 0.1 if picked.get("slot:" + name[5:], NONE) == NONE else 0.9
                else:
                    label = q["instructions"].split("「")[1].split("」")[0] if "「" in q["instructions"] else ""
                    v = 0.9 if label and label in state else 0.1
                answers[name] = {"type": "noul", "noul": v}
        tokens = self.estimate_tokens(request.content) if self.estimate_tokens else 321
        return httpx2.Response(200, json={"model": body.get("model") or "jev-1.14.0",
                                          "usage": {"input_tokens": tokens, "output_tokens": 0}, "answers": answers})
