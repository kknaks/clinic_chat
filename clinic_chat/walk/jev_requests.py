"""Jev 요청 꼴(SPEC-002 §3-3) — state · 질문 id · 32 묶음. **고르기 타입(하나 고르기)은 쓰지 않는다**(0.5.0 — RG1 · TG3).

- instructions 는 영어(`prompts/jev/*.md`) · state 는 한국어 이름 + 코드(D10).
- KD8(WP-0): instructions 에 **가리키는 것의 이름을 글자로 함께 적는다**(색인 `candidates[i]` · `kinds_catalog[k]` 만 쓰면
  목록 끝에서 옆 항목 값으로 밀린다). 이름은 앱 · 허브가 gold 에서 낸 것 그대로다 — Jev 가 만드는 것이 아니다.
- Jev 응답에서 읽는 것은 `noul` 뿐이다. Jev 가 낸 문자열을 노드 id · 종류 코드 · 입구 이름으로 쓰는 길이 없다.
"""

from __future__ import annotations

from typing import Any

from clinic_chat.clients.jev_client import Jev, noul


def base_state(question: str, build: dict | None, **kw: Any) -> dict:
    s: dict[str, Any] = {"question": question, "build": (build or {}).get("gold_hash")}
    s.update({k: v for k, v in kw.items() if v is not None})
    return s


def chunks(items: list, size: int) -> list[list]:
    size = max(1, size)
    return [items[i:i + size] for i in range(0, len(items), size)]


def fill(template: str, i: int, item: dict | None = None) -> str:
    """`{i}` · `{name}` · `{id}` 자리 채움(이름은 gold 의 것 그대로 — KD8)."""
    item = item or {}
    name = item.get("name")
    if not name and item.get("names"):
        name = item["names"][0]
    return (template.replace("{i}", str(i)).replace("{k}", str(i)).replace("{name}", str(name or ""))
            .replace("{id}", str(item.get("id") or item.get("code") or "")))


async def ask_nouls(jev: Jev, state_base: dict, items: list[dict], prefix: str, template: str,
                    batch: int, extra: dict[str, str] | None = None) -> tuple[list[float], dict[str, float], int]:
    """후보 전부를 `batch` 개씩 나눠 **전부** 평가(버리는 후보 없음 · §3-3 「32개 묶음」).

    반환: 후보 순서의 noul 목록 · extra 질문(예: answer_ready)의 값(묶음마다 받으면 가장 낮은 값 — C1) · 요청 수.
    """
    values: list[float] = []
    extra_vals: dict[str, float] = {}
    n_req = 0
    for part in chunks(items, batch) or [[]]:
        if not part and not extra:
            continue
        state = dict(state_base)
        state["candidates"] = part
        qs = {f"{prefix}_{i}": noul(fill(template, i, it)) for i, it in enumerate(part)}
        for name, instr in (extra or {}).items():
            qs[name] = noul(instr)
        a = await jev.ask(state, qs)
        n_req += 1
        for i in range(len(part)):
            values.append(a.nouls[f"{prefix}_{i}"])
        for name in extra or {}:
            v = a.nouls[name]
            extra_vals[name] = min(v, extra_vals.get(name, v))
    return values, extra_vals, n_req


async def ask_front(jev: Jev, state_base: dict, candidates: list[dict], catalog: list[dict],
                    instr: dict[str, str], batch: int) -> tuple[list[float], list[float], float, int, set[str]]:
    """단계 ①-2 — **한 요청** = `target_{i}`(후보 수) + `kind_{k}`(14) + `gap`(1). 후보가 `batch` 를 넘으면 둘째 요청부터
    `target_{i}` 만(§3-3). 후보 0 이면 `kind` · `gap` 만(CD4). 반환 = 대상 noul · 종류 noul · gap · 요청 수 · model id."""
    parts = chunks(candidates, batch) or [[]]
    targets: list[float] = []
    kinds: list[float] = []
    gap = 0.0
    models: set[str] = set()
    n = 0
    for j, part in enumerate(parts):
        state = dict(state_base)
        if part:
            state["candidates"] = part
        qs = {f"target_{i}": noul(fill(instr["target"], i, it)) for i, it in enumerate(part)}
        if j == 0:
            state["kinds_catalog"] = catalog
            for k, it in enumerate(catalog):
                qs[f"kind_{k}"] = noul(fill(instr["kind"], k, it))
            qs["gap"] = noul(instr["gap"])
        a = await jev.ask(state, qs)
        n += 1
        models.add(a.model or "")
        targets.extend(a.nouls[f"target_{i}"] for i in range(len(part)))
        if j == 0:
            kinds = [a.nouls[f"kind_{k}"] for k in range(len(catalog))]
            gap = a.nouls["gap"]
    return targets, kinds, gap, n, models
