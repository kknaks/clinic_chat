"""PR1 무리(SPEC-002 §3-7-3) — 대상을 「질문이 이름 댄 것 하나」 로 묶는다(결정론).

(i) 같은 조각(`resolve` 의 같은 `span` · `start`)에서 나온 개념 (ii) 허브 `scope(node)` 가 다른 대상을 `물려받음` · `하위 *`
(자손 전부)로 내는 개념 — 은 한 무리다(이어 붙임 = 합집합). **B(`walk.front`)와 답 키(`evalset.key_calls`)가 같은 함수.**

무리 판정 `scope` 는 방문 장부의 `calls_done` 에 넣지 않는다 — `lineup` 읽기(같은 `scope {node}`)를 가리지 않게(검수 W-4).
기록은 `visited` 꼴 줄(`purpose` = `group`)로만 돌려준다(§7-3 `breadth[].purpose` 값을 늘리지 않는다 — B-7 되적기 후보).
"""

from __future__ import annotations

from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.walk.calls import Entry
from clinic_chat.walk.kinds import TARGET4

LINK_ROLES = ("물려받음",)  # + 「하위 *」 — `role` 이 「하위」 로 시작


def _linked(role: str) -> bool:
    return role in LINK_ROLES or role.startswith("하위")


async def scope_links(hub: HubRest, nodes: list[str]) -> tuple[dict[str, dict[str, str]], list[dict]]:
    """대상마다 `scope(node)` 한 번(같은 노드는 한 번) → {대상: {holder 노드: role}} · visited 꼴 기록."""
    links: dict[str, dict[str, str]] = {}
    visited: list[dict] = []
    for n in dict.fromkeys(nodes):
        ra = await hub.read_all("scope", {"node": n})
        if not ra.complete:
            raise RuntimeError(f"무리 판정 scope 끝까지 못 읽음: {ra.error}")
        gh = (ra.build or {}).get("gold_hash")
        for cur in ra.cursors or [None]:
            visited.append({"build": gh, "node": n, "tool": "scope", "facet": None, "cursor": cur,
                            "purpose": "group", "range": "", "kind": []})
        d: dict[str, str] = {}
        for h in ra.items:
            t, c, role = h.get("holder_table"), h.get("holder_code"), str(h.get("role") or "")
            if t and c and _linked(role):
                d.setdefault(f"{t}:{c}", role)
        links[n] = d
    return links, visited


def group_targets(targets: list[Entry], links: dict[str, dict[str, str]] | None = None
                  ) -> tuple[list[list[Entry]], list[str | None]]:
    """대상 → 무리(대상 순서 유지 · 무리 순서 = 첫 대상 순서) · 무리마다 묶은 까닭(`span` | `scope` | 혼자면 None)."""
    ts = [t for t in targets if t.table in TARGET4]
    parent = list(range(len(ts)))
    why: dict[int, set[str]] = {}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(i: int, j: int, by: str) -> None:
        a, b = find(i), find(j)
        if a != b:
            parent[max(a, b)] = min(a, b)
        why.setdefault(min(a, b), set()).add(by)
        why.setdefault(max(a, b), set()).add(by)

    for i in range(len(ts)):
        for j in range(i + 1, len(ts)):
            a, b = ts[i], ts[j]
            if a.span and a.span == b.span and a.start is not None and a.start == b.start:
                join(i, j, "span")
            elif links and (b.node in (links.get(a.node) or {}) or a.node in (links.get(b.node) or {})):
                join(i, j, "scope")
    out: dict[int, list[Entry]] = {}
    for i, t in enumerate(ts):
        out.setdefault(find(i), []).append(t)
    groups = list(out.values())
    bys: list[str | None] = []
    for root in out:
        ws = set().union(*(why.get(i, set()) for i in range(len(ts)) if find(i) == root)) if len(out[root]) > 1 else set()
        bys.append(" · ".join(x for x in ("span", "scope") if x in ws) or None)
    return groups, bys


def yes_group(g: list[Entry]) -> bool:
    """「yes 무리」 = 대상이 전부 yes 가 아닌(`uncertain_kept` · `named` 뿐) 무리가 아닌 것(검수 N-3)."""
    return not all(e.by in ("uncertain_kept", "named") for e in g)
