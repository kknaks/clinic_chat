"""RQ1 — required 를 어느 층의 행으로 채우나(SPEC-002 §3-2 RQ1 네 줄 · G12).

(대상, 정보 종류)마다 **읽은 행으로** `self` · `lower` · `inherited` 를 센다 — `layer.role` 로 가르고 서로 다른 `gid` ·
`overview` · `mechanism` 은 그 종류의 칸에 값이 있는 profile 행만(`kinds.has_kind_value` — `coverage` 와 같은 식).
값 쪽 · 속성 쪽 조회(role 없음)는 행이 있으면 `self`. `lineup` = `scope` 의 「하위 *」 행 → `lower`.

    self ≥ 1                      → met_by = self
    self 0 · lower ≥ 1            → met_by = lower      (물려받은 윗층 행만으로는 충족이 아니다)
    self 0 · lower 0 · inh ≥ 1    → met_by = inherited  (답이 「윗층의 것」 으로 밝힌다)
    셋 다 0                       → met_by = None       (미충족 — 후보 소진 · 세 조건에 걸리면 원천 미확정)

**B(`col_b`)와 답 키 스크립트(`evalset.answer_key`)가 이 모듈의 같은 함수를 쓴다**(N-2).
"""

from __future__ import annotations

from clinic_chat.walk.kinds import has_kind_value
from clinic_chat.walk.verified import response_rows, table_of

KIND_ROW_TABLES = {"adverse": ("g_adverse",), "same_day": ("g_pair",)}  # 셈은 이 표의 행(SPEC-001 §5-8 — 딸린 표 제외)
UPPER = "_inherited_from"  # 셈 전용 표지 — 윗고민으로 물려받은 응답의 행(재료 · 기록에는 안 간다)


def rows_for_count(resp: dict) -> list[dict]:
    """한 응답의 행(셈용). **머리 `inherited_from` ≠ null 인 `concern_candidates` 응답**(HG1 — 물은 고민에 자기 행 0 이라
    가장 가까운 윗고민의 행을 물려받음)의 행은 `inherited` 로 센다. 행의 `hier.match` = 「상위」 만으로는 가르지 않는다 —
    `facets` package 값 쪽의 「상위」 와 섞이지 않게(B-5)."""
    return mark_upper(resp, response_rows(resp))


def mark_upper(resp: dict, rows: list[dict]) -> list[dict]:
    up = resp.get("inherited_from")
    if not up:
        return rows
    code = up.get("concern_code") if isinstance(up, dict) else str(up)
    return [{**r, UPPER: code} for r in rows]


def layer_bucket(row: dict, side: str = "a") -> str:
    if row.get(UPPER):
        return "inherited"
    lay = (row.get("b_layer") if side == "b" else None) or row.get("layer") or row.get("a_layer") or {}
    role = str(lay.get("role") or "")
    if row.get("gold_table") == "g_scope" or table_of(row) == "g_scope":
        return "lower" if str(row.get("role") or "").startswith("하위") else "skip"
    if not role or role == "자기":
        return "self"
    if role == "물려받음":
        return "inherited"
    if role.startswith("하위"):
        return "lower"
    return "self"


def count_rows(rows: list[dict], kind: str, side: str = "a") -> dict:
    """한 (대상, 종류)의 행 → {self, lower, inherited}(서로 다른 gid)."""
    seen: dict[str, set[str]] = {"self": set(), "lower": set(), "inherited": set()}
    tables = KIND_ROW_TABLES.get(kind)
    for r in rows:
        t = table_of(r)
        if tables and t not in tables:
            continue
        if kind in ("overview", "mechanism") and t == "g_profile" and not has_kind_value(r, kind):
            continue
        b = layer_bucket(r, side)
        if b in seen:
            seen[b].add(str(r.get("gid")))
    return {k: len(v) for k, v in seen.items()}


def met_by(c: dict) -> str | None:
    if c.get("self"):
        return "self"
    if c.get("lower"):
        return "lower"
    if c.get("inherited"):
        return "inherited"
    return None


FILTER_CODE = {"adverse": "adverse_code", "condition": "condition_code"}  # BR1 — 대상이 있으면 사건 · 조건 입구는 필터로 건다


def call_side(q: dict, node: str) -> str | None:
    """조회 `q` 가 그 대상의 조회인가 → 쪽(a | b) · 아니면 None. **B(`target_rows`)와 답 키(`kinds_met`)가 같은 함수.**

    필터로 쓴 입구(`filters.adverse_code` · `filters.condition_code` — BR1)도 그 입구의 조회로 센다(코드 검수 v052 W-1):
    필터 조회의 행은 정의상 그 사건 · 조건의 행이다(좁힘이라 `coverage` 대조는 건너뜀 — `Call.narrowed`)."""
    if node in (q.get("node"), q.get("a")):
        return "a"
    if node == q.get("b"):
        return "b"
    table, _, code = node.partition(":")
    if table == "concern" and q.get("concern_code") == code:
        return "a"
    col = FILTER_CODE.get(table)
    if col and (q.get("filters") or {}).get(col) == code:
        return "a"
    return None


def target_rows(reads, node: str, kind: str) -> tuple[list[dict], str, bool]:
    """그 대상 · 그 종류의 읽기 조회(`purpose` = read · followup 제외)가 낸 행 · 쪽(a | b) · 좁힘 여부."""
    rows: list[dict] = []
    side = "a"
    narrowed = False
    for rd in reads:
        c = rd.call
        if c.purpose != "read" or c.meta.get("followup") or kind not in c.kinds:
            continue
        sd = call_side(c.request, node)
        if sd is None:
            continue
        side = sd
        narrowed = narrowed or c.narrowed()
        rows.extend(rows_for_count(rd.ra.as_response()))
    return rows, side, narrowed


def inherited_via(rows: list[dict]) -> list[str]:
    """윗고민으로 물려받은 행의 윗고민 코드(답 키 `kinds_met[].via` 와 같은 뜻 — `concern:코드`)."""
    return sorted({f"concern:{r[UPPER]}" for r in rows if r.get(UPPER)})


def required_status(required_kinds: list[str], targets: list[str], reads, coverage: dict | None = None,
                    applicable=None) -> dict:
    """required 종류 × 대상 → RQ1. `applicable(node, kind)` = 그 대상의 표에 그 종류의 조회가 있나(없으면 건너뜀 — KD4).

    `coverage` = 단계 ①-1 의 셈 `{node: {kind: {self, lower, inherited}}}` — 좁힘 없이 부른 조회에서만 대조하고
    다르면 `coverage_mismatch` 로 기록한다(판정은 읽은 행으로 · W-2).
    """
    items = []
    for k in required_kinds:
        for node in targets:
            if applicable is not None and not applicable(node, k):
                continue
            rows, side, narrowed = target_rows(reads, node, k)
            c = count_rows(rows, k, side)
            item = {"kind": k, "target": node, **c, "met_by": met_by(c), "ok": met_by(c) is not None}
            via = inherited_via(rows)
            if via:
                item["inherited_via"] = via
            cov = ((coverage or {}).get(node) or {}).get(k)
            if cov is not None and not narrowed:
                want = {x: cov.get(x) or 0 for x in ("self", "lower", "inherited")}
                item["coverage_mismatch"] = None if want == c else {"coverage": want, "read": c}
            else:
                item["coverage_mismatch"] = None
            items.append(item)
    unresolved = [f"{i['target']}:{i['kind']}" for i in items if not i["ok"]]
    return {"items": items, "ok": not unresolved,
            "unresolved": unresolved, "required": [f"{i['target']}:{i['kind']}" for i in items]}
