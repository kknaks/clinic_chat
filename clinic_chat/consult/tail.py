"""근거 꼬리(SPEC-004 §4-4) — 표식을 인자로 받는 것 말고는 `columns/col_b.py` 의 `split_tail` · `_TailFilter` 와 동작이 같다.

꼬리의 줄 번호는 대화 번호다(상담 재료가 대화 전체에서 번호를 이어 매긴다).
"""

from __future__ import annotations

import re

TAIL_LINE = re.compile(r"^\s*(\d+)\s*[:：]\s*((?:\d+(?:\s*-\s*\d+)?)(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*)\s*$")


def split_tail(text: str, mark: str) -> tuple[str, list[dict] | None, int]:
    """답 → (꼬리 뗀 본문, answer_lines | None, 꼬리 글자 수). 꼬리가 없거나 한 줄이라도 꼴이 깨졌으면 None."""
    i = text.find(mark)
    if i < 0:
        return text.rstrip(), None, 0
    body, tail = text[:i].rstrip(), text[i + len(mark):]
    out = []
    for ln in tail.strip().splitlines():
        if not ln.strip():
            continue
        m = TAIL_LINE.match(ln)
        if not m:
            return body, None, len(text) - i
        rows: list[int] = []
        for part in m.group(2).split(","):
            a, _, b = part.partition("-")
            rows.extend(range(int(a), int(b) + 1) if b.strip() else [int(a)])
        out.append({"answer_line": int(m.group(1)), "rows": rows})
    return body, (out or None), len(text) - i


class TailFilter:
    """스트리밍 delta 에서 꼬리 표식 뒤를 화면으로 흘리지 않는다(표식이 delta 둘에 걸쳐도)."""

    def __init__(self, mark: str) -> None:
        self.mark = mark
        self.buf = ""
        self.cut = False

    def __call__(self, ev: dict) -> dict | None:
        if ev.get("event") != "delta":
            return ev
        if self.cut:
            return None
        self.buf += ev.get("text") or ""
        j = self.buf.find(self.mark)
        if j >= 0:
            self.cut = True
            out, self.buf = self.buf[:j], ""
            return {**ev, "text": out} if out else None
        keep = 0
        for n in range(min(len(self.mark) - 1, len(self.buf)), 0, -1):
            if self.mark.startswith(self.buf[-n:]):
                keep = n
                break
        out = self.buf[:len(self.buf) - keep]
        self.buf = self.buf[len(self.buf) - keep:]
        return {**ev, "text": out} if out else None


CLOSE_LINE = re.compile(r"^\s*close\s*[:：]\s*(\d+)\s*$", re.IGNORECASE)


def split_close(text: str, mark: str) -> tuple[str, int | None]:
    """0.3 CS5 — 꼬리(표식 아래)의 `close: <답 줄>` 한 줄을 떼어 (나머지 글, 줄 번호 | None). 꼬리 · 줄이 없으면 None.
    떼고 남은 글은 `split_tail` 이 0.1 꼴 그대로 읽는다."""
    i = text.find(mark)
    if i < 0:
        return text, None
    head, tail = text[:i + len(mark)], text[i + len(mark):]
    said = None
    keep = []
    for ln in tail.splitlines(keepends=True):
        m = CLOSE_LINE.match(ln.strip("\r\n"))
        if m and said is None:
            said = int(m.group(1))
            continue
        keep.append(ln)
    return head + "".join(keep), said
