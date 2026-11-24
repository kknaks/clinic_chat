"""⑤ (나) 답 재료 조회(SPEC-008 §2-5 QM1 ~ QM5 · §3-0 ~ §3-4 · WORK-017 WP-6).

걷기의 그릇(`Walk` · `FrontResult` · `DepthResult`)을 상담이 지어 걷기의 결정론 함수에 넘긴다 — 걷기 코드를 베끼지 않는다.
차례 = `col_b.walk()` 와 같은 호출 순서 · 같은 값: `FrontResult`(대상 · 종류 · 범위 · coverage · 무리) → `plan_calls` → `read_calls`
→ `_labels` → `same_day_marks` → 깊이(DP8 — 이번 턴 대상인 후보만 `gate_forced` · 그 밖 `listed_only` · 걸음 한 번) → `rq1` →
`build` → `roster` → `check` → `_end_check` → `es.decide`. Jev · `value_pick` · `walk_depth` · `front()` · `resolve` 를 부르지 않는다
(값 고르기 표지 0 · 미루던 부작용 상세는 폭 읽기에서 전부 — VP8). 대상 0 인 `new` = 「입구 없음」 · `out` + 대상 0 + 종류 0 =
「범위 밖」(QM3).
"""

from __future__ import annotations

from clinic_chat.clients.hub_rest import HubError
from clinic_chat.columns.col_b import NO_B_ON, Walk, _end_check, _labels, same_day_marks
from clinic_chat.consult import kinds_pick as kp
from clinic_chat.walk import end_state as es
from clinic_chat.walk.breadth import BreadthError, read_calls
from clinic_chat.walk.calls import SITE_NAME_OFF, Entry, depth_calls, depth_candidates, depth_kind, plan_calls
from clinic_chat.walk.depth import DepthResult, rq1
from clinic_chat.walk.entry import END_NO_ENTRY, END_OUT, EntryIncomplete
from clinic_chat.walk.front import FrontResult, coverage_of_nodes
from clinic_chat.walk.gate import check
from clinic_chat.walk.groups import group_targets, scope_links
from clinic_chat.walk.kinds import ATTR_START, TARGET4
from clinic_chat.walk.material import Material, build, roster

GATE = "gate_forced"
LISTED = "listed_only"


SAFETY_ALWAYS = ("adverse", "contraindication", "recovery")  # SPEC-012 HK1
SAFETY_TARGET_TABLES = ("procedure", "modality", "procedure_case", "product")


def safety_always(k, entries) -> None:
    """SPEC-012 HK1(SPEC-011 UD5 글자대로) — 대상에 시술 · 계열 · 케이스 · 제품이 있으면 안전 종류를 늘 읽는다(낱말 목록 0 ·
    필수 아님 · 범위 판단은 2차 HF2 가 holder 부위로)."""
    if not any(e.table in SAFETY_TARGET_TABLES for e in entries):
        return
    for c in SAFETY_ALWAYS:
        if c not in k.by and k.applicable.get(c):
            k.by[c] = "safety_always"
    k.kinds = [c for c in kp.KIND_CODES if c in k.by and k.applicable.get(c)]


def entries_of(acc, site_codes: list[str], el) -> list[Entry]:
    """QM1 — 대상 = 검증 뒤 `targets`(`sure` 무관) + 이번 턴 부위(거르개 입구 — BR1)."""
    out: list[Entry] = []
    seen: set[str] = set()
    for t in acc.targets:
        e = Entry(t["table"], t["code"], t.get("name") or el.name(t["table"], t["code"]), "target")
        if e.node not in seen:
            seen.add(e.node)
            out.append(e)
    for c in site_codes:
        e = Entry("site", c, el.name("site", c), "target")
        if e.node not in seen:
            seen.add(e.node)
            out.append(e)
    return out


async def _depth(d, w: Walk, f: FrontResult) -> tuple[DepthResult, list[dict]]:
    """DP8-1 ~ DP8-6 — 후보 식 · 깊이 조회는 걷기 함수 그대로 · 결정은 이번 턴 대상이면 `gate_forced` · 그 밖 `listed_only`."""
    out = DepthResult()
    entry_nodes = {e.node for e in f.entries}
    cand_ranges = [ATTR_START] if f.attr_start else [r for r in f.ranges if depth_kind(r)]
    pending: dict = {}
    for r in cand_ranges:
        rows = [v.row for v in w.verified.rows.values() if r in v.ranges]
        for c in depth_candidates(r, rows, f.entries):
            pending.setdefault(c.id, c)
    read: list[dict] = []
    for cid, c in pending.items():
        gate = cid in entry_nodes
        out.decisions[cid] = {"decision": GATE if gate else LISTED, "noul": None, "range": c.range, "closed_as": None,
                              "reason": "DP8-2 target" if gate else "DP8-2 not_target"}
    for cid, c in pending.items():  # DP8-4 — 걸음 한 번
        if out.decisions[cid]["decision"] != GATE:
            continue
        calls = depth_calls(c.range, c, f.kinds)
        new = await read_calls(d.hub, calls, w.verified, w.ledger, w.reads, depth="deep", read_as_by_node=None,
                               follow=False)
        read.append({"id": cid, "calls": len(calls)})
        for r in new:  # 새 후보는 DP8-2 로만 가른다(읽지 않는다)
            rows = [w.verified.rows[g].row for g in r.gids if g in w.verified.rows]
            for nc in depth_candidates(c.range, rows, f.entries):
                if nc.id not in out.decisions:
                    out.decisions[nc.id] = {"decision": GATE if nc.id in entry_nodes else LISTED, "noul": None,
                                            "range": nc.range, "closed_as": None, "reason": "DP8-4 one_step"}
    out.required = rq1(f, w.reads)
    out.exhausted = not out.required.get("ok")
    return out, read


async def build_walk(d, acc, site_codes: list[str], el) -> Walk:
    """SPEC-012 HF1 — 상담 경로 걷기는 허브 부위 거르개(site_name) 없이 읽는다(이 걷기 안 모든 조회 · 깊이 조회 포함)."""
    tok = SITE_NAME_OFF.set(True)
    try:
        return await _build_walk(d, acc, site_codes, el)
    finally:
        SITE_NAME_OFF.reset(tok)


async def _build_walk(d, acc, site_codes: list[str], el) -> Walk:
    w = Walk()
    f = FrontResult()
    w.front = f
    f.kinds_by = "understand"
    aborted = None
    try:
        f.entries = entries_of(acc, site_codes, el)
        if not f.entries:  # QM3
            f.end = END_OUT if acc.out and not acc.kinds else END_NO_ENTRY
            if f.end == END_OUT:
                f.scope_gap = True
        else:
            f.coverage, f.build = await coverage_of_nodes(d.hub, [e.node for e in f.entries])
            fk = (getattr(acc, "fetch", None) or {}).get("kinds") or []  # SPEC-011 JC2 — 1차 `fetch.kinds`
            k = kp.decide(f.entries, acc.kinds, f.coverage, fetch_kinds=fk)
            safety_always(k, f.entries)  # SPEC-012 HK1 — 상담 경로만(걷기 `_decide_kinds` 와 같은 결정은 그대로 두고 덧붙임)
            f.kinds, f.required, f.offered, f.by = k.kinds, k.required, k.offered, k.by
            f.ranges, f.attr_start, f.applicable_kinds, f.kinds_by = k.ranges, k.attr_start, k.applicable, k.kinds_by
            f.scope_gap = bool(acc.out)
            targets = [e for e in f.entries if e.table in TARGET4]
            if len(targets) >= 2:  # PR1 — `front.py` 와 같은 꼴
                f.links, f.group_visited = await scope_links(d.hub, [t.node for t in targets])
                gs, bys = group_targets(targets, f.links)
                f.groups = [[t.node for t in g] for g in gs]
                f.group_by = dict(enumerate(bys))
            f.trace_front = {"kinds": [{"code": c, "by": f.by.get(c)} for c in f.kinds], "kinds_by": f.kinds_by,
                             "offered": f.offered, "ranges": f.ranges, "attr_start": f.attr_start,
                             "groups": [{"members": g, "by": f.group_by.get(i)} for i, g in enumerate(f.groups)]}
            w.ledger.visited.extend(f.group_visited)
            w.planned = plan_calls(f.entries, f.kinds, groups=f.groups or None)  # VP8-2 — 미루는 조회 없음
            await read_calls(d.hub, w.planned, w.verified, w.ledger, w.reads, read_as_by_node=None)
            await _labels(d, w)
            same_day_marks(w)
            w.depth, read = await _depth(d, w, f)
            f.trace_front["required"] = w.depth.required.get("items", [])
            f.trace_front["depth"] = {"decisions": {k2: v["decision"] for k2, v in w.depth.decisions.items()},
                                      "read": read}
            inh = {i["kind"] for i in w.depth.required.get("items", []) if i.get("met_by") == "inherited"}
            w.material = build(w.verified, decisions=w.depth.decisions, speak_names=w.labels, inherited_kinds=inh)
            w.material.roster = roster(w.reads, w.verified, {e.node for e in f.entries})
            w.gate = check(reads=w.reads, verified=w.verified, entries=f.entries, decisions=w.depth.decisions,
                           ranges=f.ranges, kinds=f.kinds, material=w.material, planned=w.planned,
                           required=w.depth.required)
            if w.gate["ok"]:
                w.end_check = await _end_check(d, w)
    except (BreadthError, EntryIncomplete) as ex:
        aborted, w.error = "hub_breadth", {"kind": "hub", "stage": "breadth", "detail": str(ex)}
    except HubError as ex:
        aborted, w.error = "hub", {"kind": "hub", "stage": "material", "detail": str(ex)[:300]}
    except Exception as ex:  # noqa: BLE001 — `col_b.walk` 와 같이 그 턴만 「실행 중단」
        aborted, w.error = "hub", {"kind": type(ex).__name__, "stage": "material", "detail": str(ex)[:300]}
    if aborted is None and w.gate and not w.gate["ok"]:
        aborted = "gate:" + ",".join(w.gate["fails"])
    w.end_state, w.reason = es.decide(aborted=aborted, entry_end=f.end, unsettled=(w.end_check or {}).get("result"),
                                      required_ok=bool(w.depth and w.depth.required.get("ok")), gate_ok=True)
    if w.end_state in NO_B_ON:
        w.material = Material()
    return w


def depth_trace(w: Walk) -> dict:
    """장부 `trace.depth` — `{decisions{id: gate_forced | listed_only}, read[]}`."""
    return (w.front.trace_front or {}).get("depth") or {"decisions": {}, "read": []} if w.front else \
        {"decisions": {}, "read": []}
