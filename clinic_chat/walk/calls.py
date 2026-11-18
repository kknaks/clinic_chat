"""대상 × 정보 종류 → 읽기 조회 계획(SPEC-002 §3-1 BR1 ~ BR4 · §3-2 「폭」 · 「깊이」 칸 · §3-2a 「읽는 조회」).

**B 의 단계 ② 와 답 키 스크립트(WP-7)가 이 모듈의 같은 함수(`plan_calls`)를 쓴다** — BR3 을 코드 공유로 지킨다.

- BR1: 입력은 (i) 표가 고정한 것(도구 · facet · `roles` · `classes` · `package_kinds` · `hier` 기본) (ii) 단계 ① 이 낸 대상뿐.
  대상이 둘 이상이면 값 · 속성 입구를 필터로 건다(`filters.adverse_code` · `filters.condition_code` · `site`).
  단 덮은 대상의 조회에는 그 안에 든 조각을 걸지 않고 · 사이 값(TG2) 입구는 거른 조회 + 안 거른 조회(`filter_entries`).
- BR2: 그 밖의 좁힘(`self_pair` · `same_day_only` · 입구에서 오지 않은 `filters`)은 걸지 않는다.
- 「폭」 칸에 있어도 읽기로 하지 않은 종류의 조회는 **내지 않는다**(0.5.0). 순서·간격은 회피 갈래를 읽지 않는다(BR3 · Q25).
- 상품 이름 길(`offer_name_contains`)은 속성 시작의 조회를 끄지 않고 **더한다**(BR3 · R2-F1).
- BR4: 「물은 칸」 은 좁힘이 아니다 — 여기 입력에 없다.
- 0.6 BR6: yes 가 아닌(`uncertain_kept` · `named`) 거르개 입구는 거른 조회 + 안 거른 조회(거른 쪽에 「좁힌 것」 표지 — `meta.mark`).
- 0.6 PR · BR7 · W-4 · B-3: 짝 조회의 끝은 무리로(`groups` — `walk.groups`) — 무리 하나면 대상마다 한 끝 · 둘 이상이면 무리 사이
  두 끝 · yes 아닌 무리가 끼면 yes 무리(없으면 무리마다)의 한 끝을 함께.
- 0.6 BR5: 사건 필터(`filters.adverse_code`) 조회의 상세는 필터 없이 내지 않고, 응답의 사건 위험 id 마다 이어 읽는다(`followup_calls`).
"""

from __future__ import annotations

import itertools
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from clinic_chat.walk.kinds import (
    ATTR_FACETS,
    ATTR_KIND_FACETS,
    ATTR_START,
    KIND_CODES,
    PAIR_KINDS,
    TARGET4,
    range_of,
)
from clinic_chat.walk.ranges import (
    ATTR4,
    ATTR_DEPTH,
    BY_CODE,
    OFFER_NAME,
    PAIR_CLASSES,
    ROLES_BASE,
    ROLES_PRODUCT_OFFER,
    ROLES_TARGET,
    SAME_DAY_PACKAGE_KINDS,
    SITE_NAME_FACETS,
    is_attr_start,
)

KEY_TOOLS = ("facets", "concern_candidates", "pair")  # 속성 행을 내는 호출만 키 명세에(§9-5 — scope · resolve 제외)
NARROWED_MARK = "그 부위(사건 · 조건)로 좁힌 것"  # BR6 · W-4 표지(SPEC-002 §3-7-3 BR6 의 말 그대로 — W-2 코디)
PURPOSES = ("read", "depth", "end_state", "label")  # §7-3 `breadth[].purpose`(+ 깊이 · N-1 표지용 — B-1)
OFFER_START = ("product", "procedure", "procedure_case", "offer")  # 상품 범위 시작 표 — 없으면 상품 이름 길


@dataclass
class Entry:
    table: str
    code: str
    name: str = ""
    by: str = "rule"  # rule | target | named | uncertain_kept | noul(①-4 입구 고르기 확정) | set | carried(상담 이어 묻기)
    read_as: dict | None = None  # {by: "entry_pick" | "TG2" | "TG3", table, code} — 답이 밝힐 표지(프롬프트 낱말)
    via: str | None = None  # TG2 경유
    span: str | None = None  # resolve `matched.span`(B 단계 ① 만 — 키 · 깊이 입구는 None)
    covered_by: str | None = None  # resolve `matched.covered_by` — 이 조각을 덮은 긴 일치의 글자
    start: int | None = None  # resolve `matched.start`(PR1 (i) — 같은 `span` · `start` 면 한 무리)
    norm: str | None = None  # resolve 후보의 `norm`(VP3 — 값 입구 이름이 이 조각을 품나)

    @property
    def node(self) -> str:
        return f"{self.table}:{self.code}"


@dataclass
class Call:
    tool: str
    request: dict
    kinds: tuple[str, ...] = ()
    ranges: tuple[str, ...] = ()
    purpose: str = "read"
    candidate: str | None = None  # depth 일 때 그 후보 id
    meta: dict = field(default_factory=dict)

    @property
    def range(self) -> str:
        return self.ranges[0] if self.ranges else ""

    def node(self) -> str:
        r = self.request
        return str(r.get("node") or r.get("a") or (f"concern:{r['concern_code']}" if r.get("concern_code") else "")
                   or r.get("offer_name_contains") or "")

    def key(self) -> str:
        """같은 조회를 한 번만 — 방문 장부 키의 (도구 · 입력) 부분(cursor · build 는 장부가 붙인다)."""
        return self.tool + "?" + json.dumps(self.request, ensure_ascii=False, sort_keys=True)

    def narrowed(self) -> bool:
        """좁힘 입력(두 끝 · 필터 · site)이 걸렸나 — 그러면 `coverage` 셈과 대조하지 않는다(W-2)."""
        r = self.request
        return bool(r.get("b") or r.get("filters") or r.get("site") or r.get("offer_name_contains"))

    def spec(self) -> dict:
        return {"tool": self.tool, "request": self.request}

    def as_dict(self) -> dict:
        return asdict(self)


def _squash(x: str | None) -> str:
    return "".join(str(x or "").split())


def covers(owner: Entry | None, piece: Entry) -> bool:
    """`piece` 가 `owner` 이름 안에 든 덮인 조각인가(`piece.covered_by` = `owner.span`)."""
    return bool(owner is not None and piece.covered_by and owner.span and _squash(piece.covered_by) == _squash(owner.span))


WEAK_BY = ("uncertain_kept", "named")  # BR6 · W-4 — yes 가 아닌 대상 · 입구


def _mark(calls: list[Call], mark: str = "narrowed") -> list[Call]:
    for c in calls:
        c.meta["mark"] = mark
    return calls


def filter_entries(owner: Entry | None, entries: list[Entry], table: str) -> tuple[list[Entry], bool]:
    """`owner` 의 조회에 거르개로 걸 `table` 입구(부위 · 사건 · 조건) · 거르지 않은 조회도 함께 낼지.

    - **덮은 대상의 조회에는 그 안에 든 조각을 거르개로 쓰지 않는다**(_RESUME §2 11-17 「덮인 조각 규칙」 — 「부위+고민」 꼴
      긴 이름의 조회를 그 안의 부위로 좁히지 않는다 · 덮은 이름이 이미 그 부위를 품는다). 떨어진 이름(부위 · 고민을 따로 말함)은
      거르개 그대로(HG3).
    - 거르개 입구가 yes 가 아닌 것(TG2 `uncertain_kept` · TG3 `named`)이면 **거른 조회 + 안 거른 조회**(코드 검수 v052 W-2 ·
      0.6 BR6 — yes 가 아닌 입구는 혼자 폭을 좁히지 못한다). yes(`target`)인 떨어진 입구는 거른 조회만(U-9)."""
    fs = [e for e in entries if e.table == table and not covers(owner, e)]
    return fs, any(e.by in WEAK_BY for e in fs)


def _site_filters(entries: list[Entry], facet: str, owner: Entry | None = None) -> list[dict | None]:
    """부위 입구 필터 — **부위마다 하나씩**(사건 · 조건 입구와 같은 식 · 코드 검수 v05 W-2). 걸 수 없으면 `[None]`."""
    if facet not in SITE_NAME_FACETS or len(entries) < 2:
        return [None]
    sites, plain = filter_entries(owner, entries, "site")
    if not sites:
        return [None]
    out: list[dict | None] = [{"site_name": x} for x in dict.fromkeys(e.name for e in sites)]
    return [None, *out] if plain else out


def _per_site(kind: str, rng: str, facet: str, node: str, roles: tuple[str, ...] | None, entries: list[Entry],
              base: dict | None = None, owner: Entry | None = None, mark: bool = False) -> list[Call]:
    """부위마다 조회 하나. 안 거른 조회를 함께 내면(BR6) 거른 조회에 표지 · `mark` = 위에서 이미 좁힌 조회(사건 · 조건)."""
    fs = _site_filters(entries, facet, owner)
    both = len(fs) > 1 and fs[0] is None
    out = []
    for f in fs:
        c = _facets(kind, rng, facet, node, roles, {**(base or {}), **(f or {})} or None)
        if (both and f is not None) or (mark and c.request.get("filters")):
            c.meta["mark"] = "narrowed"
        out.append(c)
    return out


def _facets(kind: str, rng: str, facet: str, node: str, roles: tuple[str, ...] | None,
            filters: dict | None = None, purpose: str = "read") -> Call:
    req: dict[str, Any] = {"node": node, "facet": facet}
    if roles is not None:
        req["roles"] = list(roles)
    if filters:
        req["filters"] = dict(filters)
    return Call("facets", req, (kind,), (rng,), purpose)


def _pairs(targets: list[Entry], groups: list[list[str]] | None = None) -> list[tuple[Entry, Entry | None, str | None]]:
    """짝 조회의 끝(PR2 ~ PR4 · BR7 · W-4 · B-3) — (a, b | None, 표지).

    무리 하나 → 대상마다 한 끝. 무리 둘 이상 → 서로 다른 무리의 대상 짝 전부를 두 끝(0 행이어도 한 끝으로 넓히지 않는다).
    yes 가 아닌 무리(대상이 `uncertain_kept` · `named` 뿐)가 끼면 두 끝 + yes 무리의 한 끝을 함께(W-4 — 두 끝 행에 「좁힌 것」
    표지) · yes 무리가 0 이면 무리마다 한 끝을 함께(B-3 · 코디 결정)."""
    if not targets:
        return []
    gid = {}
    for i, g in enumerate(groups or []):
        for n in g:
            gid.setdefault(n, i)
    for t in targets:  # 무리 정보가 없는 대상은 혼자 무리(옛 꼴 — 대상끼리 두 끝)
        if t.node not in gid:
            gid[t.node] = len(gid) + 10_000
    if len({gid[t.node] for t in targets}) <= 1:
        return [(t, None, None) for t in targets]
    by_g: dict[int, list[Entry]] = {}
    for t in targets:
        by_g.setdefault(gid[t.node], []).append(t)
    weak = {g for g, ts in by_g.items() if all(t.by in WEAK_BY for t in ts)}
    out: list[tuple[Entry, Entry | None, str | None]] = [
        (a, b, "narrowed" if weak else None) for a, b in itertools.combinations(targets, 2) if gid[a.node] != gid[b.node]]
    if weak:
        ones = [g for g in by_g if g not in weak] or list(by_g)  # B-3 — yes 무리 0 이면 무리마다
        why = "w4" if len(ones) < len(by_g) else "b3"
        out += [(t, None, why) for g in ones for t in by_g[g]]
    return out


def _pair(kind: str, rng: str, a: Entry, b: Entry | None) -> Call:
    req: dict[str, Any] = {"a": a.node}
    if b is not None:
        req["b"] = b.node
    if kind in PAIR_CLASSES:
        req["classes"] = list(PAIR_CLASSES[kind])
    if kind == "same_day":
        req["package_kinds"] = list(SAME_DAY_PACKAGE_KINDS)
    return Call("pair", req, (kind,), (rng,))


def _target_calls(t: Entry, kind: str, rng: str, entries: list[Entry], by_table: dict[str, list[Entry]]) -> list[Call]:
    n = t.node
    advs, adv_plain = filter_entries(t, entries, "adverse")
    conds, cond_plain = filter_entries(t, entries, "condition")
    multi = len(entries) >= 2
    if kind in ("overview", "mechanism"):
        return _per_site(kind, rng, "profile", n, ROLES_TARGET, entries, owner=t)
    if kind == "indication":
        return _per_site(kind, rng, "indication", n, ROLES_TARGET, entries, owner=t)
    if kind == "outcome":
        return _per_site(kind, rng, "outcome", n, ROLES_TARGET, entries, owner=t)
    if kind == "lineup":
        return [Call("scope", {"node": n}, (kind,), (rng,))]
    if kind == "adverse":
        out = []
        if advs and multi:
            for a in advs:
                out.extend(_per_site(kind, rng, "adverse", n, ROLES_TARGET, entries, {"adverse_code": a.code}, owner=t,
                                     mark=adv_plain))
        if not (advs and multi) or adv_plain:
            out.extend(_per_site(kind, rng, "adverse", n, ROLES_TARGET, entries, owner=t))
            # BR5 — 사건을 이름 대지 않은 조회(+ BR6 의 안 거른 조회)는 상세 전부. 사건 필터 조회의 상세는 그 응답의 사건
            # 위험 id 로 이어 읽는다(`followup_calls` — B 와 답 키가 같은 길)
            out.extend(_per_site(kind, rng, "adverse_detail", n, ROLES_TARGET, entries, owner=t))
        return out
    if kind == "recovery":
        return [_facets(kind, rng, "recovery", n, ROLES_TARGET)]
    if kind == "contraindication":
        if conds and multi:
            out = [_facets(kind, rng, "contraindication", n, ROLES_TARGET, {"condition_code": c.code}) for c in conds]
            if cond_plain:
                _mark(out)
            return out + ([_facets(kind, rng, "contraindication", n, ROLES_TARGET)] if cond_plain else [])
        return _per_site(kind, rng, "contraindication", n, ROLES_TARGET, entries, owner=t)
    if kind == "package":
        return [_facets(kind, rng, "package", n, ROLES_TARGET)]
    if kind == "offer":
        return [_facets(kind, rng, "offer", n, ROLES_PRODUCT_OFFER if t.table == "product" else ROLES_TARGET)]
    return []


def _value_calls(e: Entry, kind: str, rng: str, entries: list[Entry], has_targets: bool) -> list[Call]:
    """값 쪽 시작(대상 4표가 아닌 입구) — 고민 · 사건 · 조건 · 효과 · 상품 · 패키지."""
    n = e.node
    if e.table == "concern":
        if kind == "indication":
            fs, plain = filter_entries(e, entries, "site")  # 덮은 고민에 그 안 부위 조각은 거르개 아님 · TG2 부위는 둘 다
            sites = list(dict.fromkeys(x.name for x in fs))
            whole = Call("concern_candidates", {"concern_code": e.code}, (kind,), (rng,))
            if not sites or len(entries) < 2:
                return [whole]
            per = [Call("concern_candidates", {"concern_code": e.code, "site": x}, (kind,), (rng,)) for x in sites]
            return [whole, *_mark(per)] if plain else per
        if kind == "package":
            return [_facets(kind, rng, "package", n, None)]
    if e.table == "adverse" and kind == "adverse" and not has_targets:  # 대상이 있으면 필터로 건다(BR1)
        return _per_site(kind, rng, "adverse", n, None, entries, owner=e)
    if e.table == "condition" and kind == "contraindication" and not has_targets:
        return [_facets(kind, rng, "contraindication", n, None)]
    if e.table == "effect" and kind == "outcome":
        return [_facets(kind, rng, "outcome", n, None)]
    if e.table == "offer" and kind == "offer":
        return [_facets(kind, rng, "offer", n, None)]
    if e.table == "package" and kind == "package":
        return [_facets(kind, rng, "package", n, None)]
    return []


def _span_groups(targets: list[Entry]) -> list[list[str]]:
    """PR1 (i) 만 — 같은 `span` · `start` 의 대상끼리(허브 없이 · `groups` 를 안 준 부름)."""
    out: dict[tuple, list[str]] = {}
    for t in targets:
        k = (t.span, t.start) if t.span and t.start is not None else ("", t.node)
        out.setdefault(k, []).append(t.node)
    return list(out.values())


def merge(calls: list[Call]) -> list[Call]:
    """같은 조회는 한 번만(순서 유지) — 종류 · 범위는 합친다(같은 profile 을 개요 · 원리가 함께 읽는 꼴 · RG7)."""
    out: dict[str, Call] = {}
    for c in calls:
        k = c.key()
        if k in out:
            o = out[k]
            o.kinds = tuple(dict.fromkeys((*o.kinds, *c.kinds)))
            o.ranges = tuple(dict.fromkeys((*o.ranges, *c.ranges)))
        else:
            out[k] = c
    return list(out.values())


def plan_calls(entries: list[Entry], kinds, *, purpose: str = "read", groups: list[list[str]] | None = None) -> list[Call]:
    """대상 × 읽기로 한 종류 → 허브 조회(§3-2a 「읽는 조회」). B · 답 키 · (가) 판정용이 같은 함수.

    `groups` = PR1 무리(대상 노드 목록들 — `walk.groups.group_targets`). 없으면 같은 조각(`span` · `start`)으로만 묶는다."""
    ks = [k for k in KIND_CODES if k in set(kinds)]
    attr = is_attr_start(e.table for e in entries)
    by_table: dict[str, list[Entry]] = {}
    for e in entries:
        by_table.setdefault(e.table, []).append(e)
    targets = [e for e in entries if e.table in TARGET4]
    out: list[Call] = []
    if attr:
        for a in (e for e in entries if e.table in ATTR4):
            for k in ks:
                for f in ATTR_KIND_FACETS.get(k, ()):
                    if f in ATTR_FACETS[a.table]:
                        out.append(_facets(k, ATTR_START, f, a.node, None))
    else:
        for t in targets:
            for k in ks:
                if k in PAIR_KINDS:
                    continue
                rng = range_of(k, t.table, False)
                if rng:
                    out.extend(_target_calls(t, k, rng, entries, by_table))
        for k in ks:
            if k in PAIR_KINDS and targets:
                rng = range_of(k, targets[0].table, False)
                for a, b, why in _pairs(targets, groups if groups is not None else _span_groups(targets)):
                    c = _pair(k, rng or "", a, b)
                    if why == "narrowed":
                        c.meta["mark"] = "narrowed"
                    elif why:
                        c.meta["widened"] = why  # W-4 · B-3 로 함께 부른 한 끝(기록 · 보고서)
                    out.append(c)
        for e in entries:
            if e.table in TARGET4 or e.table in ATTR4 or e.table == OFFER_NAME:
                continue
            for k in ks:
                rng = range_of(k, e.table, False)
                if rng:
                    out.extend(_value_calls(e, k, rng, entries, bool(targets)))
    if "offer" in ks:  # 상품 이름 길 — 상품 시작 표의 입구가 없을 때 front 가 더한 `offer_name` 입구(R2-F1 — 더한다)
        for o in by_table.get(OFFER_NAME, []):
            out.append(Call("facets", {"facet": "offer", "offer_name_contains": o.name}, ("offer",),
                            (ATTR_START if attr else "offer",)))
    for c in out:
        c.purpose = purpose
    return merge(out)


RISK_TABLE = "adverse_risk"  # BR5 — 사건 위험 행의 silver 표(SPEC-001 부록 A · `g_adverse.silver_table`)


def adverse_detail_by_risk(call: Call, risk_ids, *, kinds=("adverse",)) -> list[Call]:
    """BR5 · VP2 — 그 조회(같은 `node` · `roles` · 부위 필터)의 `adverse_detail` 을 사건 위험 id 마다 하나씩(허브 `filters` 는
    칸마다 값 하나 — SPEC-001 §4-4 · WP-0 H2). 순서 = id 오름차순(결정론)."""
    r = call.request
    base = {k: v for k, v in (r.get("filters") or {}).items() if k != "adverse_code"}
    out = []
    for rid in sorted({str(x) for x in risk_ids if x}):
        c = _facets(kinds[0], call.range, "adverse_detail", r["node"], tuple(r["roles"]) if r.get("roles") else None,
                    {**base, "adverse_risk_id": rid}, purpose=call.purpose)
        c.kinds, c.ranges = tuple(kinds), call.ranges
        out.append(c)
    return out


def risk_ids(rows: list[dict]) -> list[str]:
    """`g_adverse` 행 가운데 silver 표가 사건 위험인 행의 서로 다른 `silver_id`(오름차순)."""
    return sorted({str(x.get("silver_id")) for x in rows if x.get("gold_table", "g_adverse") == "g_adverse"
                   and x.get("silver_table") == RISK_TABLE and x.get("silver_id")})


def followup_calls(call: Call, response: dict) -> list[Call]:
    """폭 조회가 낸 응답에서 결정론으로 이어지는 조회(B · 답 키 같은 함수):
    - 부위·고민의 `groups` 후보마다 `facets` profile(§3-2 · §9-5)
    - **BR5**(0.6) — `facets` adverse 를 `filters.adverse_code` 로 읽었으면 그 응답 행의 사건 위험 id 마다 `adverse_detail`."""
    if call.tool == "facets" and call.request.get("facet") == "adverse" and \
            (call.request.get("filters") or {}).get("adverse_code") and call.request.get("node"):
        out = adverse_detail_by_risk(call, risk_ids(list(response.get("rows") or [])))
        for c in out:
            c.meta.update({"followup": True, "from_risk": True, **({"mark": call.meta["mark"]}
                                                                   if call.meta.get("mark") else {})})
        return out
    if call.tool != "concern_candidates":
        return []
    out, seen = [], set()
    for g in response.get("groups") or []:
        node = f"{g.get('candidate_table')}:{g.get('candidate_code')}"
        if node in seen or not g.get("candidate_code"):
            continue
        seen.add(node)
        c = _facets("indication", call.range, "profile", node, ROLES_BASE, purpose=call.purpose)
        c.ranges = call.ranges
        c.meta["followup"] = True
        c.meta["from_group"] = node
        out.append(c)
    return out


def key_calls(calls: list[Call]) -> list[Call]:
    """답 키 명세에 드는 것(§9-5 — 속성 행을 내는 호출 + **`lineup` 의 `scope`**).

    답 키 고치기 1 K6: 0.5.1 은 `scope` 를 통째로 뺐다 → `expected_kinds` 에 `lineup` 이 있어도 키가 하위 목록을 한 번도
    안 읽어 `kinds_met` 에 lineup 이 없었다(Q01 ~ Q05). `lineup` 을 읽는 `scope` 는 명세에 넣는다 — 그 `g_scope` 행은 여전히
    키 `rows[]` 에 넣지 않고(§9-5 · 재검수2 X-5 ①) `lineup_rows[]` 와 RQ1 셈(`lower`)에만 쓴다."""
    return [c for c in calls if c.tool in KEY_TOOLS or (c.tool == "scope" and "lineup" in c.kinds)]


# §3-2 「폭」 칸 — 범위마다 그 범위의 조회 전부(종류로 적음). (가) 판정용 「그 범위의 나머지 조회」 의 기준.
RANGE_BREADTH = {"procedure": ("overview", "mechanism", "indication", "outcome", "lineup"),
                 "concern": ("indication", "package"), "adverse": ("adverse", "recovery"),
                 "combo": ("combo", "package"), "same_day": ("same_day",), "sequence": ("order_interval",),
                 "avoid": ("avoid", "contraindication"), "offer": ("offer",), ATTR_START: ("overview", "indication")}


def range_kinds(entries: list[Entry], ranges) -> list[str]:
    """그 범위(들)의 「폭」 칸 종류 전부 — (가) 판정용 「그 대상의 그 범위 조회 전부」(§5 · §3-2 「폭」 칸)."""
    ks = {k for r in ranges for k in RANGE_BREADTH.get(r, ())}
    return [k for k in KIND_CODES if k in ks]


# ───────────── 깊이(§3-2 「깊이」 칸 — 승인된 초안 · O-5 ④) ─────────────

@dataclass
class Candidate:
    id: str  # 허브가 낸 node_id(`표:코드`) — Jev 가 만든 것이 아니다
    name: str
    relation: str
    range: str
    rows: dict | None = None  # coverage 셈(§3-3 `candidates[].rows`)

    def as_state(self) -> dict:
        d = {"id": self.id, "name": self.name, "relation": self.relation}
        if self.rows:
            d["rows"] = self.rows
        return d


def _other_end(row: dict) -> tuple[str, str, str] | None:
    side = row.get("a_side")
    if side == "from":
        t, c, n = row.get("to_table"), row.get("to_code"), row.get("to_name")
    elif side == "to":
        t, c, n = row.get("from_table"), row.get("from_code"), row.get("from_name")
    else:
        return None
    return (t, c, n or "") if t and c else None


def depth_kind(range_code: str) -> str | None:
    if range_code == ATTR_START:
        return ATTR_DEPTH
    rdef = BY_CODE.get(range_code)
    return rdef.depth if rdef else None


def depth_candidates(range_code: str, rows: list[dict], entries: list[Entry]) -> list[Candidate]:
    """폭(또는 깊이)에서 읽은 행 → 깊이 후보. 같은 id 는 한 번. 깊이 칸이 「없음」 이면 빈 목록."""
    kind = depth_kind(range_code)
    targets = [e for e in entries if e.table in TARGET4]
    out: dict[str, Candidate] = {}

    def add(t: str | None, c: str | None, name: str | None, rel: str) -> None:
        if t and c:
            node = f"{t}:{c}"
            if node not in out:
                out[node] = Candidate(node, name or "", rel, range_code)

    for row in rows:
        gt = row.get("gold_table")
        if kind == "adverse_value_side" and not targets and gt == "g_adverse":
            add(row.get("target_table"), row.get("target_code"), row.get("target_name"), "g_adverse:target")
        elif kind in ("other_end", "other_end_one_side") and gt == "g_pair":
            if kind == "other_end_one_side" and len(targets) != 1:
                continue
            oe = _other_end(row)
            if oe:
                node = f"{oe[0]}:{oe[1]}"
                if node not in out:
                    out[node] = Candidate(node, oe[2], f"g_pair:{row.get('question_class')}", range_code)
        elif kind == "condition_side" and not targets and gt == "g_contraindication":
            add(row.get("target_table"), row.get("target_code"), row.get("target_name"), "g_contraindication:target")
        elif kind == "offer_items" and gt == "g_offer" and row.get("procedure_code"):
            add("procedure", row.get("procedure_code"), row.get("procedure_name"), "g_offer:procedure")
        elif kind == ATTR_DEPTH:
            for tt, tc, tn in (("subject_table", "subject_code", "name_ko"), ("target_table", "target_code", "target_name"),
                               ("candidate_table", "candidate_code", "candidate_name")):
                if row.get(tt) in TARGET4 and row.get(tc):
                    add(row.get(tt), row.get(tc), row.get(tn), f"{gt}:{(row.get('attr') or {}).get('column')}")
                    break
    return list(out.values())


def depth_calls(range_code: str, cand: Candidate, read_kinds) -> list[Call]:
    """후보 하나의 깊이 조회. 속성 시작은 「읽기로 한 종류의 조회를 그 대상으로」(§3-2 속성 시작 행)."""
    kind = depth_kind(range_code)
    table, code = cand.id.split(":", 1)
    out: list[Call] = []
    if kind == "adverse_value_side":
        out = [_facets("adverse", range_code, "adverse_detail", cand.id, ROLES_TARGET),
               _facets("recovery", range_code, "recovery", cand.id, ROLES_TARGET)]
    elif kind in ("other_end", "other_end_one_side", "condition_side", "offer_items"):
        out = [_facets("overview", range_code, "profile", cand.id, ROLES_BASE)]
    elif kind == ATTR_DEPTH:
        out = plan_calls([Entry(table, code, cand.name)], read_kinds)
        for c in out:
            c.ranges = (ATTR_START,)
    for c in out:
        c.candidate = cand.id
        c.purpose = "depth"
    return out
