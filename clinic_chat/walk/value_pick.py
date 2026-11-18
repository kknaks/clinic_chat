"""0.6 값 고르기(VP1 ~ VP3 · SPEC-002 §3-7-4) · 사건 상세의 읽는 차례(BR5 · 코디 W-7 — B-4 제안대로) — ② 폭 읽기 뒤 · ③ 깊이 앞.

- 도는 조건(VP1): **required 인** 값 종류(`adverse` · `contraindication` · `outcome` · `indication` 가운데 `by` = yes · 1위)가 있고
  그 값 표(사건 · 조건 · 효과 · 고민)의 입구가 대상에 하나도 없다. 값 입구가 있으면 요청 0.
- 후보 = 대상 × 그 종류에서 **읽은 행의 서로 다른 값 코드**(키 코드 칸 — `walk.keycodes.VALUE_KIND`) · 이름 = 그 행의 이름 칸.
  읽은 행 밖의 개념을 후보로 넣지 않는다(TG4 그대로). Jev Noul `value_{i}` 32 묶음.
- VP2: yes 인 값 = 값 입구(`entry.picked[].by` = `value_pick`). 행을 빼지 않는다 — 그 값의 행에 표지 「질문이 가리킨 값」.
- VP3: 값 입구의 입구 이름(`resolve` 목록 꼴 `norm`)이 다른 대상 조각(부위 · 사건 · 조건 거르개 입구)의 `norm` 을 품으면 그 조각은
  덮인 것 → 그 조각으로 건 거르개를 걷은 조회를 다시 읽는다(방문 장부 · 표지 합침 그대로).
- W-7: 값 종류에 `adverse` 가 있으면(값 입구 없음) `adverse_detail` 은 VP 뒤로 미룬다 — yes 사건이 있으면 그 사건 행의 위험 id 로
  (`calls.adverse_detail_by_risk`) — 조회 하나 단위: 미룬 상세마다 같은 node · 부위 필터의
  adverse 응답 행 가운데 yes 사건 행의 id 만(키 `followup_calls` 와 같은 꼴 · 코드 검수 W-1) · 못 내면 미룬 조회 전부. 「걸기 전」 수는 `coverage`(adverse) 셈으로 남긴다(N-2).
- 못 잡으면 지금과 같다(그 말은 놓친다 — O-21 ① 의 나머지 · O-35). 지어내지 않는다.
"""

from __future__ import annotations

from clinic_chat import prompts
from clinic_chat.walk.calls import Call, adverse_detail_by_risk, plan_calls, risk_ids
from clinic_chat.walk.jev_requests import ask_nouls, base_state
from clinic_chat.walk.keycodes import NAME_COLS, VALUE_KIND
from clinic_chat.walk.kinds import TARGET4

FILTER_TABLES = ("site", "adverse", "condition")  # 거르개로 거는 입구(BR1)
VALUE_MARK = "질문이 가리킨 값"  # VP2 표지(SPEC-002 §3-7-4 VP2 의 말 그대로)


def needed(f) -> list[str]:
    """VP1 — 돌 값 종류(required · yes / 1위 · 그 값 표 입구 없음 · 대상이 있음)."""
    if f is None or f.end or not any(e.table in TARGET4 for e in f.entries):
        return []
    tables = {e.table for e in f.entries}
    return [k for k in VALUE_KIND if k in f.required and f.by.get(k) in ("yes", "top") and VALUE_KIND[k][0] not in tables]


def deferred(calls: list[Call], kinds: list[str]) -> list[Call]:
    """W-7 — 값 고르기가 도는 `adverse` 의 상세 조회(VP 뒤로 미룬다)."""
    if "adverse" not in kinds:
        return []
    return [c for c in calls if c.tool == "facets" and c.request.get("facet") == "adverse_detail"]


def candidates(verified, kinds: list[str]) -> list[dict]:
    out: dict[str, dict] = {}
    for k in kinds:
        vt, rt, col = VALUE_KIND[k]
        names = NAME_COLS.get(col, ())
        for v in verified.rows.values():
            if v.gold_table != rt or k not in v.kinds or not v.row.get(col):
                continue
            code = f"{vt}:{v.row[col]}"
            if code not in out:
                out[code] = {"id": code, "name": next((str(v.row[n]) for n in names if v.row.get(n)), ""), "kind": k}
    return list(out.values())


def _uses(c: Call, pieces) -> bool:
    flt = c.request.get("filters") or {}
    return any((e.table == "site" and flt.get("site_name") == e.name) or (e.code and e.code in flt.values())
               for e in pieces)


def _site_filters(c: Call) -> dict:
    return {k: v for k, v in (c.request.get("filters") or {}).items() if k != "adverse_code"}


def _own_risk_ids(dc: Call, reads, adv_yes: set[str]) -> list[str]:
    """W-1 — 미룬 상세 조회 `dc` 와 같은 `node` · `roles` · 부위 필터로 읽은 `adverse` 응답 행 가운데 yes 사건 행의 위험 id."""
    rows = []
    for r in reads:
        c = r.call
        if (c.tool == "facets" and c.request.get("facet") == "adverse" and c.request.get("node") == dc.request.get("node")
                and c.request.get("roles") == dc.request.get("roles") and _site_filters(c) == _site_filters(dc)):
            rows.extend(x for x in r.ra.as_response().get("rows") or [] if x.get("adverse_code") in adv_yes)
    return risk_ids(rows)


async def run(question: str, d, w, kinds: list[str], defer: list[Call], read) -> dict:
    """VP1 ~ VP3 + 미룬 상세 읽기. `read(calls)` = 같은 장부 · verified 로 읽는 함수(col_b 가 준다)."""
    s, f = d.settings, w.front
    cands = candidates(w.verified, kinds)
    tr: dict = {"kinds": kinds, "candidates": len(cands), "requests": 0, "per_candidate": [], "picked": [],
                "uncovered_filters": [], "deferred_detail_calls": len(defer)}
    yes: list[dict] = []
    if cands:
        vals, _, n_req = await ask_nouls(d.jev, base_state(question, w.build()), [{"id": c["id"], "name": c["name"]}
                                                                               for c in cands],
                                         "value", prompts.jev("value"), s.jev_batch_size)
        tr["requests"] = n_req
        f.jev_requests += n_req
        for c, v in zip(cands, vals, strict=True):
            dec = "yes" if v >= s.jev_noul_yes_at else ("no" if v <= s.jev_noul_no_at else "between")
            tr["per_candidate"].append({"code": c["id"], "kind": c["kind"], "noul": v, "decision": dec})
            if dec == "yes":
                yes.append(c)
    yes_codes = {c["id"] for c in yes}
    for c in yes:
        f.trace_entry.setdefault("picked", []).append({"id": c["id"], "by": "value_pick", "via": None})
    tr["picked"] = sorted(yes_codes)
    # VP2 — 그 값의 행에 표지(행은 빼지 않는다)
    for v in w.verified.rows.values():
        for k in kinds:
            vt, rt, col = VALUE_KIND[k]
            if v.gold_table == rt and v.row.get(col) and f"{vt}:{v.row[col]}" in yes_codes:
                v.marks["value_pick"] = True
    # VP3 — 값 입구 이름이 품는 거르개 조각 → 그 거르개를 걷은 조회를 다시 읽는다
    if yes:
        norms: dict[str, set[str]] = {}
        for vt in sorted({c["id"].split(":", 1)[0] for c in yes}):
            ra = await d.hub.read_all("resolve", {"tables": [vt]})
            for r in ra.items:
                node = f"{r.get('entity_table')}:{r.get('entity_code')}"
                if node in yes_codes and r.get("norm"):
                    norms.setdefault(node, set()).add(str(r["norm"]))
        allnorm = {n for ns in norms.values() for n in ns}
        cov = [e for e in f.entries if e.table in FILTER_TABLES and e.norm and e.node not in yes_codes
               and any(e.norm in n and e.norm != n for n in allnorm)]
        if cov:
            tr["uncovered_filters"] = [e.node for e in cov]
            again = plan_calls([e for e in f.entries if e not in cov], f.kinds, groups=f.groups or None)
            new = [c for c in again if w.ledger.seen(c) is None]
            defer = [c for c in defer if not _uses(c, cov)]  # 걷힌 거르개로 건 미룬 상세는 버리고 걷은 꼴로
            more_defer = deferred(new, kinds)
            defer = [*defer, *[c for c in more_defer if c.key() not in {x.key() for x in defer}]]
            new = [c for c in new if c not in more_defer]
            tr["reread_calls"] = len(new)
            w.planned.extend(new)
            await read(new)
    # W-7 · BR5 — 미룬 상세: yes 사건이 있으면 조회 하나 단위로 — 미룬 상세 조회마다 **같은 node · 같은 부위 필터로 읽은 adverse
    # 응답 행 가운데 yes 사건 행**의 위험 id 만 건다(키의 `calls.followup_calls` 와 같은 꼴 · W-1). 아무 조회도 id 를 못 내면 전부
    if defer:
        adv_yes = {c["id"].split(":", 1)[1] for c in yes if c["kind"] == "adverse"}
        per = [(dc, _own_risk_ids(dc, w.reads, adv_yes)) for dc in defer] if adv_yes else []
        if any(ids for _, ids in per):
            calls = []
            for dc, ids in per:
                for c in adverse_detail_by_risk(dc, ids):
                    c.meta["value_pick"] = True
                    calls.append(c)
            nodes = sorted({dc.request.get("node") for dc in defer})
            before = sum(sum((f.coverage.get(n, {}).get("adverse") or {}).get(x) or 0
                             for x in ("self", "lower", "inherited")) for n in nodes)
            got = await read(calls)
            tr["br5"] = {"risk_ids": len({i for _, ids in per for i in ids}), "calls": len(calls),
                         "per_call_ids": [len(ids) for _, ids in per],
                         "before_rows_coverage_adverse": before,
                         "after_detail_rows": sum(len(r.gids) for r in got)}
        else:
            w.planned.extend(defer)
            await read(defer)
            tr["br5"] = None
    tr["deferred"] = bool(defer)
    return tr


__all__ = ["VALUE_MARK", "deferred", "needed", "run"]
