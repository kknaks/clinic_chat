"""금지 낱말 검사(SPEC-006 VO10 · WORK-014 §9) — 기록만.

`prompts/consult.json` 의 `voice_check[]`(정규식 · 초안)를 꼬리 뗀 답 본문에 돌려 걸린 것을 장부 `consult.voice` 에 남긴다.
**답을 고치거나 다시 돌리지 않는다.** 걸린 글자는 맞은 조각 그대로(문맥 없이).
"""

from __future__ import annotations

import re


def check(body: str, patterns: tuple[str, ...] | list[str]) -> dict:
    hits: list[dict] = []
    for pat in patterns:
        for m in re.finditer(pat, body or ""):
            hits.append({"pattern": pat, "text": m.group(0)})
    return {"hits": hits, "n": len(hits)}
