"""① 입구 목록 적재(SPEC-008 §2-1 LD1 ~ LD8 · WORK-017 WP-1).

- 허브 `resolve` 목록 꼴(`tables` = 다섯 표 · `text` 없음)을 `next_cursor: null` 까지 → `g_entry` 행 전부(LD1).
- 개념 하나 = (`entity_table`, `entity_code`) 하나 · 이름 = `entity_name` · 별칭 = 그 개념의 다른 행 `text` 가운데 이름과 글자가
  다른 것(행 순서 · 같은 글자 한 번 — LD2).
- 글 꼴 = 시안 「# 목록」: `## <표 이름> (table = <t>)` · `<코드> <이름> (= <별칭>, …)` · 표 순서 = `TABLES` · 표 안 = 허브 순서(LD3).
  `aliases=False` 면 괄호만 뺀다(LD4) · sha = 글의 sha256(LD6).
- 캐시 = 프로세스 메모리 · 키 = (`gold_hash`, `aliases`)(LD5). 턴마다 `health` 를 부르지 않는다 — 대화의 `gold_hash` 와 같은 캐시가
  있으면 그대로 · 없거나 다르면 다시 받는다. 실패(허브 오류 · 쪽 넘김 미완) = `EntryListError`(LD7 · 반쪽 목록 0).
- 다섯 표 밖 행은 목록에 없다(LD8).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

TABLES = ("concern", "site", "modality", "procedure", "product", "effect", "procedure_case")  # 0.5 N1 — 효과 · 케이스 더함
TABLE_TITLES = {"concern": "\uace0\ubbfc", "site": "\ubd80\uc704", "modality": "\uacc4\uc5f4", "procedure": "\uc2dc\uc220", "product": "\uc81c\ud488", "effect": "\ud6a8\uacfc", "procedure_case": "\ucf00\uc774\uc2a4"}  # 시안 「# 목록」 머리(고민 · 부위 · 계열 · 시술 · 제품 — LLM 에 가는 글)


class EntryListError(RuntimeError):
    pass


@dataclass(frozen=True)
class Item:
    table: str
    code: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntryList:
    gold_hash: str
    text: str
    sha: str
    aliases: bool
    items: dict[tuple[str, str], Item] = field(default_factory=dict)
    names: dict[str, tuple[str, str]] = field(default_factory=dict)  # 이름 → (표, 코드)(처음 것)

    def name(self, table: str, code: str) -> str:
        it = self.items.get((table, code))
        return it.name if it else ""

    def has(self, table: str, code: str) -> bool:
        return (table, code) in self.items

    def counts(self) -> dict[str, int]:
        out = {t: 0 for t in TABLES}
        for t, _ in self.items:
            out[t] += 1
        return out


_CACHE: dict[tuple[str, bool], EntryList] = {}


def build(rows: list[dict], gold_hash: str, *, aliases: bool) -> EntryList:
    """`g_entry` 행 → 목록(결정론 — 같은 행이면 글자까지 같다)."""
    concepts: dict[tuple[str, str], dict] = {}
    for r in rows:
        t, c = r.get("entity_table"), r.get("entity_code")
        if t not in TABLES or not c:
            continue
        e = concepts.setdefault((t, c), {"name": r.get("entity_name") or r.get("text") or "", "al": []})
        txt = r.get("text") or ""
        if txt and txt != e["name"] and txt not in e["al"]:
            e["al"].append(txt)
    lines: list[str] = []
    items: dict[tuple[str, str], Item] = {}
    names: dict[str, tuple[str, str]] = {}
    for t in TABLES:
        lines.append(f"## {TABLE_TITLES[t]} (table = {t})")
        for (tt, c), e in concepts.items():
            if tt != t:
                continue
            items[(t, c)] = Item(t, c, e["name"], tuple(e["al"]))
            names.setdefault(e["name"], (t, c))
            lines.append(f"{c} {e['name']}" + (f" (= {', '.join(e['al'])})" if aliases and e["al"] else ""))
    text = "\n".join(lines)
    return EntryList(gold_hash, text, hashlib.sha256(text.encode("utf-8")).hexdigest(), aliases, items, names)


async def load(hub, *, aliases: bool, cache: bool = True, gold_hash: str | None = None) -> EntryList:
    """`gold_hash` = 대화의 판(`Conv.gold_hash`). 비면(대화의 첫 턴) 목록 첫 쪽(`limit` 1)의 머리로 지금 판을 확인해
    그 판의 캐시가 있으면 쓰고 없으면 통째로 다시 받는다(검수 L-3 — 허브 판이 바뀐 뒤 새 대화가 옛 목록으로 뽑지 않게)."""
    if cache:
        key = (gold_hash, aliases) if gold_hash else _LAST.get(aliases)
        if key and key in _CACHE and not gold_hash:  # 검수 L-3 — 첫 턴은 목록 첫 쪽(1 행)의 머리로 판을 확인한다
            try:
                head = await hub.call("resolve", {"tables": list(TABLES), "limit": 1})
                now = str((head.get("build") or {}).get("gold_hash") or "")
            except Exception:  # noqa: BLE001 — 확인이 깨지면 아래에서 통째로 다시 받는다(실패면 LD7)
                now = ""
            key = (now, aliases) if now else None
        if key and key in _CACHE:
            return _CACHE[key]
    try:
        ra = await hub.read_all("resolve", {"tables": list(TABLES)})
    except Exception as e:  # noqa: BLE001 — 허브 오류는 전부 목록 실패(LD7)
        raise EntryListError(f"{type(e).__name__}: {str(e)[:200]}") from e
    if not ra.complete:
        raise EntryListError(ra.error or "incomplete")
    gh = str((ra.build or {}).get("gold_hash") or "")
    el = build(list(ra.items), gh, aliases=aliases)
    if cache and gh:
        _CACHE[(gh, aliases)] = el
        _LAST[aliases] = (gh, aliases)
    return el


_LAST: dict[bool, tuple[str, bool]] = {}


def clear_cache() -> None:
    _CACHE.clear()
    _LAST.clear()
