"""2차 읽기 — 1차 `fetch` 대로 자료 줄을 고른다(SPEC-011 §5 G1 ~ G7 · 결정론 · 판단 0).

「읽고 잘라 내기」(§5 끝): 허브 호출은 지금 그대로 두고, 읽은 줄마다 **전체 · 이름만 · 뺌** 을 정한다. 뺌 · 이름만은
`material.renumber` 가 받아 줄을 싣지 않거나 이름 칸만 싣는다.

차례(측정 2차 규칙 5판 `fetch-plan/measure.select` 와 같은 뜻):
1. 종류가 없는 줄(요약 · 표지 없음) → 전체.
2. G3 명시 대상(주인 코드가 이번 targets 에 있거나 주인 이름이 말에 있음) → 전체.
3. G3 안전 종류(adverse · recovery · contraindication · avoid · caution) → 전체(UD5 — 명동점 밖 하위도 전체).
4. G2 종류가 `g2_kinds` 밖 → 뺌(KD9 덧붙임 · KS7 기본 덧붙임 · 안 물은 종류).
5. G6 `about = topic` — 고민 이름 칸이 있는 줄은 지금 주제 고민과 같은 것만 · 고민 칸이 없는 대상 줄은 지금 주제 진료항목의
   허브 코드에 닿는 것만 → 그 밖 뺌.
6. G4 층 — 명동점 진료항목에 닿는 하위는 늘 · 그 밖 층은 `layers` 대로(0.6.0 프롬프트는 `layers` 를 내지 않아 늘 `sub`).
7. G4 범위 — `scope = branch` 이고 주인이 명동점에 안 닿는 하위 · 후보면 → 이름만.
8. G5 `lineup = names` 면 lineup 종류 줄 → 이름만.
9. 그 밖 → 전체.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from clinic_chat.walk.kinds import ATTR4, ATTR_START, DEFAULTS, ENTRY_KIND, HOME_RANGE, kinds_for_table, range_of
from clinic_chat.walk.ranges import ROLES_TARGET

FULL, NAMES, DROP = "full", "names", "drop"
SAFETY = frozenset({"adverse", "recovery", "contraindication", "avoid", "caution"})
SELF_ROLES = ROLES_TARGET[:2]  # 자기 · 물려받음(`walk/ranges.py` 의 층 이름 — 같은 값)
SUB_ROLES = ROLES_TARGET[2:]  # 하위 시술 · 하위 케이스 · 하위 제품
LAYERS = {"self": {SELF_ROLES[0]}, "inherit": set(SELF_ROLES), "sub": {*SELF_ROLES, *SUB_ROLES}}
OWNER_TABLES = ("procedure_case", "product", "procedure", "modality")
MD_TABLES = ("procedure_case", "procedure", "product")
NAME_KEY = re.compile(r"name|^n$")
NAME_COLS = ("holder_name", "name_ko", "procedure_name", "candidate_name", "subject_name", "target_name")
PAREN_TAIL = re.compile(r"\(.*\)$")


@dataclass
class Plan:
    """2차가 실제로 쓰는 값(장부 `consult.fetch` — LG2)."""

    about: str = "targets"
    kinds: list[str] = field(default_factory=list)
    layers: str = "sub"
    scope: str = "branch"
    lineup: str = "none"
    source: str = "default"
    fixed: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, fetch: dict | None) -> Plan:
        f = fetch or {}
        return cls(about=f.get("about") or "targets", kinds=list(f.get("kinds") or []), layers=f.get("layers") or "sub",
                   scope=f.get("scope") or "branch", lineup=f.get("lineup") or "none",
                   source=f.get("from") or "default", fixed=list(f.get("fixed") or []))

    def view(self) -> dict:
        return {"about": self.about, "kinds": self.kinds, "layers": self.layers, "scope": self.scope,
                "lineup": self.lineup, "from": self.source, "fixed": self.fixed}

    def brief(self) -> dict:
        """AR2 — 「앱 판정」 에 싣는 한 줄(답 단계가 안 실린 종류를 「정보 없음」 으로 말하지 않게)."""
        return {"about": self.about, "kinds": self.kinds, "scope": self.scope, "lineup": self.lineup}


@dataclass
class Line:
    """줄 하나의 판단 재료 — 앱은 걷기 줄(`Material.lines` + verified 행)에서, 재생 셈은 장부 줄에서 짓는다."""

    key: str  # gid(앱) 또는 줄 번호(재생)
    kinds: list[str]
    layer: dict | None
    silver_table: str | None
    silver_id: str | None
    row: dict  # 이름 · 고민 칸을 읽는 행(허브 행 그대로 · 재생은 묶음 머리 same + 줄)


@dataclass
class Ctx:
    targets: set[str]  # 이번 턴 대상 코드
    question: str
    md: dict[str, set[str]]  # 명동점 진료항목에 닿는 허브 코드(표 → 코드) — 매핑 `case_codes` · `procedure_codes` · `products`
    g2: set[str]
    topic_concerns: set[str] = field(default_factory=set)  # G6 — 지금 주제 고민 이름
    topic_codes: set[str] = field(default_factory=set)  # G6 — 지금 주제 진료항목의 허브 코드("table:code")


def md_sets(kos) -> dict[str, set[str]]:
    if kos is None:
        return {}
    return {"procedure_case": set(kos.by_case), "procedure": set(kos.by_proc), "product": set(kos.by_product)}


def topic_codes(kos, items) -> set[str]:
    out: set[str] = set()
    for c in items or []:
        h = ((kos.item.get(c) or {}).get("hub") or {}) if kos is not None else {}
        out |= {f"procedure_case:{x}" for x in h.get("case_codes") or []}
        out |= {f"procedure:{x}" for x in h.get("procedure_codes") or []}
        out |= {f"product:{p['code']}" for p in h.get("products") or [] if p.get("code")}
    return out


def g2_kinds(tables: list[str], kinds, plan: Plan) -> set[str]:
    """G2 — `fetch.kinds` ∪ `kinds` ∪ KS3 입구 종류 · 그 종류가 입구 어느 것에도 안 맞으면 KS6 기본 묶음. KD9 없음.
    `about = topic` 이면 대상에 지금 주제의 고민 칸이 든다(고민 입구 → indication)."""
    ts = list(tables) + (["concern"] if plan.about == "topic" else [])
    attr = bool(ts) and all(t in ATTR4 for t in ts)
    by = set(kinds or []) | set(plan.kinds)
    for t in ts:
        e = ENTRY_KIND.get(t)
        if e and range_of(e, t, attr):
            by.add(e)
    if not any(range_of(x, t, attr) for x in by for t in ts):
        for t in ts:
            home = ATTR_START if attr else HOME_RANGE.get(t)
            ks = [x for x in DEFAULTS.get(home or "", ()) if range_of(x, t, attr)] or list(kinds_for_table(t, attr))
            by |= set(ks)
    return by


def owner(ln: Line) -> tuple[str | None, str, str | None]:
    """(주인 「표:코드」, 이름, 층 role) — 층 표지가 있으면 그 holder · 대상 표 행이면 그 행 · 그 밖 None(명동점 판단 없음)."""
    lay = ln.layer if isinstance(ln.layer, dict) else None
    if lay:
        h = lay.get("holder") or {}
        key = f"{h.get('holder_table')}:{h.get('holder_code')}" if h.get("holder_table") and h.get("holder_code") else None
        return key, str(h.get("holder_name") or ""), lay.get("role")
    if ln.silver_table in OWNER_TABLES and ln.silver_id:
        nm = next((str(ln.row[c]) for c in NAME_COLS if ln.row.get(c)), "")
        role = ln.row.get("role") if ln.row.get("holder_name") else None
        return f"{ln.silver_table}:{ln.silver_id}", nm, role
    return None, "", None


def md_reach(key: str | None, md: dict[str, set[str]]) -> bool | None:
    if not key:
        return None
    t, _, c = key.partition(":")
    return (c in md[t]) if t in md else None


def decide(lines: list[Line], plan: Plan, ctx: Ctx) -> dict[str, tuple[str, str]]:
    """줄마다 (전체 | 이름만 | 뺌, 까닭)."""
    out: dict[str, tuple[str, str]] = {}
    for ln in lines:
        kinds = set(ln.kinds or [])
        if not kinds:
            out[ln.key] = (FULL, "no_kind")
            continue
        key, name, role = owner(ln)
        code = (key or "").partition(":")[2]
        nm = PAREN_TAIL.sub("", name)
        if (code and code in ctx.targets) or (len(nm) >= 2 and nm in ctx.question):
            out[ln.key] = (FULL, "explicit")
            continue
        if kinds & SAFETY:
            out[ln.key] = (FULL, "safety")
            continue
        if not kinds & ctx.g2:
            out[ln.key] = (DROP, "kind")
            continue
        if plan.about == "topic":
            cn = {ln.row.get("concern_name"), ln.row.get("asked_concern_name")} - {None, ""}
            if ctx.topic_concerns and cn and not cn & ctx.topic_concerns:
                out[ln.key] = (DROP, "topic")
                continue
            if not cn and key and ctx.topic_codes and key not in ctx.topic_codes:
                out[ln.key] = (DROP, "topic")
                continue
        reach = md_reach(key, ctx.md)
        lay_role = role if role in LAYERS["sub"] else None
        if not (reach and lay_role in SUB_ROLES) and lay_role and lay_role not in LAYERS.get(plan.layers, LAYERS["sub"]):
            out[ln.key] = (DROP, "layer")
            continue
        if plan.scope == "branch" and reach is False and role not in SELF_ROLES:
            out[ln.key] = (NAMES, "branch")
            continue
        if "lineup" in kinds and plan.lineup == "names":
            out[ln.key] = (NAMES, "lineup")
            continue
        out[ln.key] = (FULL, "read")
    return out


def names_only(d: dict) -> dict:
    """이름만 줄 · 머리 — `*name*` 칸(+ 줄 번호)만 남긴다."""
    return {k: v for k, v in d.items() if NAME_KEY.search(k)}


def app_lines(w) -> list[Line]:
    """앱 걷기 결과 → 판단 재료(verified 행 그대로 — 이름 · 고민 칸)."""
    out = []
    for x in w.material.lines:
        v = w.verified.rows.get(x["gid"]) if w.verified else None
        out.append(Line(key=x["gid"], kinds=list(x.get("kind") or []), layer=x.get("layer"),
                        silver_table=x.get("silver_table"), silver_id=x.get("silver_id"),
                        row=dict(v.row) if v is not None else {}))
    return out
