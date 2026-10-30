"""정규화 — `semantic.json` `dictionary.resolve_rules.normalize` 그대로(SPEC-006 §5-1 · 문서 = 코드).

NFKC → 라틴 악센트 접기(NFKD 결합 부호 제거 · NFC) → 입력 접기 fold → 공백 제거 → 괄호 strip_chars 지움(안 글자는 남김)
→ 전부 소문자. `fold` · `strip_chars` 는 SEMANTIC_JSON 에서 읽는다. 답 이름 · 원장 문자열 · 사전 표현에 같은 함수 하나.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path


def load_rules(semantic_json: str | Path) -> dict:
    d = json.loads(Path(semantic_json).read_text(encoding="utf-8"))
    return d["dictionary"]["resolve_rules"]


def make_normalizer(rules: dict) -> Callable[[str], str]:
    fold: dict[str, str] = dict(rules["fold"])
    strip_re = re.compile("[" + re.escape(rules["strip_chars"]) + "]")

    def norm(s: str) -> str:
        t = unicodedata.normalize("NFKC", str(s))
        t = unicodedata.normalize(
            "NFC", "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))
        )
        for a, b in fold.items():
            t = t.replace(a, b)
        t = re.sub(r"\s+", "", t)
        t = strip_re.sub("", t)
        return t.lower()

    return norm
