"""QS6 — 부위 → 고민 거꾸로 색인(SPEC-008 §2-5 QS6 · C8-1 · WORK-017 WP-5).

- 짓는 때 = 그 `gold_hash` 로 QY7 이 **처음 서는 턴**(서버 시작 때 미리 짓지 않는다) · 프로세스 메모리 캐시.
- 재료 = 목록의 고민마다 `concern_candidates(concern_code, limit=1)` 응답 머리 `sites[]` 가운데 `match` = `직접` →
  `site_code → [concern_code]`(목록 순서). 허브 계약 변경 0 · `plan.ALLOWED` 의 `concern_candidates` 안이다.
- 지은 턴에만 장부 `consult.site_index{built, concerns, ms, calls}`.
"""

from __future__ import annotations

import asyncio
import time

from clinic_chat.consult.sites import from_response

DIRECT = "\uc9c1\uc811"  # 허브 `sites[].match` 값 「직접」(앱 문장 아님)
CONC = 8

_INDEX: dict[tuple[str, str], dict[str, list[str]]] = {}  # (gold_hash, 목록 sha)


async def build(hub, el) -> tuple[dict[str, list[str]], dict]:
    t0 = time.perf_counter()
    codes = [c for (t, c) in el.items if t == "concern"]
    sem = asyncio.Semaphore(CONC)
    got: dict[str, list[dict]] = {}
    errors = 0

    async def one(code: str) -> None:
        nonlocal errors
        async with sem:
            try:
                r = await hub.concern_candidates(code, limit=1)
            except Exception:  # noqa: BLE001 — 한 고민의 실패는 그 고민만 빠진다(기록에 수)
                errors += 1
                return
            got[code] = from_response(r)
    await asyncio.gather(*(one(c) for c in codes))
    idx: dict[str, list[str]] = {}
    for code in codes:  # 목록 순서
        for p in got.get(code) or []:
            if p.get("match") == DIRECT and p.get("site_code"):
                xs = idx.setdefault(p["site_code"], [])
                if code not in xs:
                    xs.append(code)
    rec = {"built": True, "concerns": len(codes), "ms": round((time.perf_counter() - t0) * 1000, 1),
           "calls": len(codes), **({"errors": errors} if errors else {})}
    return idx, rec


async def concerns_of_site(hub, el, site_code: str) -> tuple[list[str], dict | None]:
    """→ (묶인 고민 코드, 지은 턴이면 장부 기록 · 아니면 None)."""
    idx = _INDEX.get((el.gold_hash, el.sha))
    rec = None
    if idx is None:
        idx, rec = await build(hub, el)
        if el.gold_hash:  # 검수 L-4 — 실패한 고민만 빠진 색인도 캐시한다(되풀이 260 호출 0 · 실패 수는 그 턴 `errors`)
            _INDEX[(el.gold_hash, el.sha)] = idx
    return list(idx.get(site_code) or []), rec


def clear_cache() -> None:
    _INDEX.clear()
