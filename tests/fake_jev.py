"""모의 Jev — 실물 `AsyncTypeSafeClient` 에 `httpx2.MockTransport` 를 끼워 SDK 요청 · 응답 꼴을 그대로 지난다(옛 틀 :1-12 · 22-30).

옛 체계 규칙(`INFO_RULES` 등)은 없다. 판단은 시험이 주는 함수: `choice_fn(name, criteria, state) → (key, probs)` ·
`noul_fn(name, instructions, state) → float`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
from typesafe_sdk import RetryPolicy

from clinic_chat.clients.jev_client import Jev, make_client


class FakeJev:
    def __init__(self, choice_fn=None, noul_fn=None, *, status: int | None = None, timeout: bool = False,
                 model: str = "jev-test-1") -> None:
        self.choice_fn = choice_fn
        self.noul_fn = noul_fn
        self.status = status
        self.timeout = timeout
        self.model = model
        self.requests: list[dict] = []

    def jev(self, settings) -> Jev:
        return Jev(make_client(settings, transport=httpx2.MockTransport(self.handle), compression=False,
                               retry=RetryPolicy(max_retries=0)))

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.timeout:
            raise httpx2.ReadTimeout("fixture timeout", request=request)
        body = json.loads(request.content)
        self.requests.append(body)
        if self.status:
            return httpx2.Response(self.status, json={"error": {"type": "fixture", "message": "fixture error"}})
        state = body["state"]
        answers: dict[str, Any] = {}
        for name, q in body["questions"].items():
            if q["type"] == "choice":
                keys = list(q["criteria"])
                got = self.choice_fn(name, q["criteria"], state) if self.choice_fn else keys[0]
                if isinstance(got, tuple):
                    c, probs = got
                else:
                    c = got
                    probs = {k: (0.05 / max(1, len(keys) - 1)) for k in keys}
                    probs[c] = 0.95
                answers[name] = {"type": "choice", "choice": c, "confidence": round(max(probs.values()), 4),
                                 "probabilities": probs}
            else:
                v = self.noul_fn(name, q.get("instructions"), state) if self.noul_fn else 0.9
                answers[name] = {"type": "noul", "noul": v}
        return httpx2.Response(200, json={"model": self.model, "usage": {"input_tokens": 10, "output_tokens": 0},
                                          "answers": answers})
