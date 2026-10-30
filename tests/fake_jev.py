"""모의 Jev — 실물 `AsyncTypeSafeClient` 에 `httpx2.MockTransport` 를 끼워 SDK 요청 · 응답 꼴을 그대로 지난다."""

from __future__ import annotations

import json
from typing import Any

import httpx2
from typesafe_sdk import RetryPolicy

from clinic_chat.clients.jev_client import make_client

NONE = "해당 없음"


class FakeJev:
    def __init__(self, intent: str | None = None, choices: dict[str, str] | None = None,
                 flat: set[str] | None = None, status: int | None = None, timeout: bool = False) -> None:
        self.intent = intent
        self.choices = choices or {}
        self.flat = flat or set()  # 확률을 고르게(낮은 신뢰)
        self.status = status
        self.timeout = timeout
        self.requests: list[dict] = []

    def client(self, settings):
        return make_client(settings, transport=httpx2.MockTransport(self.handle), compression=False,
                           retry=RetryPolicy(max_retries=0))

    def _pick(self, name: str, q: dict, state: str) -> str:
        crit: dict[str, Any] = q["criteria"]
        if name == "intent" and self.intent:
            return self.intent
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
                if name.startswith("said:"):
                    v = 0.1 if picked.get("slot:" + name[5:], NONE) == NONE else 0.9
                else:
                    label = q["instructions"].split("「")[1].split("」")[0] if "「" in q["instructions"] else ""
                    v = 0.9 if label and label in state else 0.1
                answers[name] = {"type": "noul", "noul": v}
        return httpx2.Response(200, json={"model": body.get("model") or "jev-1.14.0",
                                          "usage": {"input_tokens": 321, "output_tokens": 0}, "answers": answers})
