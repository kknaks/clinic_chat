"""큰 폭을 넘기는 꼴 — 쪽 나눔(SPEC-002 §3-6 FT4 · FT5). 묶음 · 줄 번호 · 싣는 칸은 `walk.material`.

- FT4 입력 한도를 넘으면 묶음 경계에서 쪽을 나눈다(한 묶음이 넘으면 그 묶음만 행 경계에서) — 줄 번호는 쪽을 넘어 잇는다.
- FT5 행 · 후보 · 칸을 버리는 길은 없다 — 쪽 나눔으로도 못 실으면 「실행 중단」(`FoldTooLarge`).
"""

from __future__ import annotations

from clinic_chat.walk.material import to_lines


class FoldTooLarge(RuntimeError):
    pass


def est_tokens(text: str) -> int:
    """보수적 어림 — utf-8 바이트 / 3(한국어 · JSON 이 섞인 입력)."""
    return len(text.encode("utf-8")) // 3 + 1


def _part(g: dict, rows: list[dict]) -> dict:
    return {**g, "rows": rows, "lines": [r["n"] for r in rows]}


def pages(groups: list[dict], max_tokens: int, overhead_tokens: int = 0) -> list[list[dict]]:
    """FT4 — 묶음 경계에서 쪽 나눔 · 한 묶음이 넘으면 그 묶음만 행 경계에서. 한 행도 못 실으면 FoldTooLarge."""
    budget = max_tokens - overhead_tokens
    if budget <= 0:
        raise FoldTooLarge("입력 한도가 지시 길이보다 작다")
    units: list[dict] = []
    for g in groups:
        if est_tokens(to_lines([g])) <= budget:
            units.append(g)
            continue
        part: list[dict] = []
        for r in g["rows"]:
            if est_tokens(to_lines([_part(g, [*part, r])])) > budget:
                if not part:
                    raise FoldTooLarge(f"묶음 {g['group']} 의 행 하나가 입력 한도보다 크다")
                units.append(_part(g, part))
                part = [r]
            else:
                part.append(r)
        if part:
            units.append(_part(g, part))
    out: list[list[dict]] = []
    cur: list[dict] = []
    for u in units:
        if cur and est_tokens(to_lines([*cur, u])) > budget:
            out.append(cur)
            cur = []
        cur.append(u)
    if cur or not out:
        out.append(cur)
    return out
