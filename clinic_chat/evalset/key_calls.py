"""37 문항 호출 명세(WORK-002 §8 6-b) — `gold-build.py` 의 `QUESTIONS` 구조 값 → `data/eval-set/calls/<id>.json`.

- `gold-build.py` 를 **실행하지 않는다**: 파일을 글로 읽어 구문 트리에서 `QUESTIONS` 목록의 리터럴만 꺼낸다
  (`ast.literal_eval` 로 값이 되는 것만 · 안 되면 멈춤). 그 파일은 돌리면 gold CSV 를 다시 쓴다.
- 질문 문장 칸은 옮기지 않는다(구조 값만). 이름 → 코드는 허브 `resolve`(같은 조각 전체 맞춤 · 범위 시작 표 먼저).
- 0.5.1: 명세 = **기대 대상 × 기대 정보 종류**의 조회 — `walk/calls.py` 의 `plan_calls` 를 B 와 같은 객체로(BR3).
  `expected_kinds[]` 초안 = `questions.csv` 의 `gold_tables` → §3-2a 표(결정론 · 질문 문장을 해석하지 않는다 · `KIND_OF_TABLE`).
  `g_pair` 는 문항 범위로 가르고(조합 = combo · 동시 = same_day · 순서·간격 = order_interval · 회피 = avoid) · `g_profile` 은
  개요 · 원리 둘 다(B-3 — 사용자가 키 확인 때 고른다) · `g_scope` 는 시술 범위에서만 `lineup`(다른 범위에서는 층 길) ·
  가를 수 없으면 지어내지 않고 `kinds_notes` 로 대조 요약에 올린다. (가) 판정용 호출(`judge_calls`)도 같은 함수로 적는다.
- (옛) 폭 조회 목록은 `walk/calls.py` 의 같은 함수(BR3). BR2 밖의 좁힘(`classes` 를 문항마다 · `self` · `same_day_only` · `k`)은
  옮기지 않고 `narrowing_dropped` 로 적는다 — 대조 요약에 올린다.
- BR3(범위 표 고정 갈래): **순서·간격 범위는 회피 갈래를 읽지 않는다**(고정 갈래 = 순서 · 간격 · 재시술 간격 — 코디 결정 ·
  앱 검수 Q25 · 「묻지 않은 안전 정보를 붙이지 않는다」). gold-build 가 범위 표 갈래 밖의 갈래를 걸었으면 그 갈래를
  `range_excluded.classes` 로 적는다 — 그 갈래의 표본은 키에 **안 드는 것이 기대**다(`answer_key` 의 `sample_check.out_of_range`).
- 변환 앞뒤로 gold 폴더 해시가 같은지 본다.

    uv run python -m clinic_chat.evalset.key_calls --from-gold-build "$SM/gold/gold-build.py"
    uv run python -m clinic_chat.evalset.key_calls --from-set [--ids N01,N02]     # 37 밖 · 확정된 문항(W-8)

`--from-set`: 세트 파일에서 `source != "37"` · `status = confirmed` 인 문항만. 입구 = 항목의 `entries[]`(없으면
`expected_entries[]`) — `표:코드` 글 또는 `{table, code, name}` · 범위 = 항목의 `range`(한국어 이름). 폭 조회는 같은 `breadth_calls`.
입구가 없는 문항(범위 밖 · 이름 없음)은 `calls = []` 명세 — 키의 기대 종료 상태는 세트 값(`answer_key.expected_state` 의 `set` 길).
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
from pathlib import Path

from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.config import Settings, load_settings
from clinic_chat.evalset.loader import kinds_of_entry, load_set, set_dir
from clinic_chat.walk.calls import Entry, key_calls, plan_calls, range_kinds
from clinic_chat.walk.groups import group_targets, scope_links
from clinic_chat.walk.kinds import KIND_CODES, TARGET4, range_of
from clinic_chat.walk.ranges import ATTR4, ATTR_START, BY_NAME, PAIR_CLASSES, is_attr_start, start_tables

STRUCT_KEYS = ("id", "range", "type", "entry", "other", "subject", "adverse", "offer_name", "need_time",
               "classes", "package_kinds", "self", "same_day_only", "k")
NARROWING = ("self", "same_day_only", "k")
ASKED_TIME = ["g_adverse.onset_text", "g_adverse.duration_text"]


class LiteralError(RuntimeError):
    pass


def read_questions(path: str | Path) -> list[dict]:
    src = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "QUESTIONS" for t in node.targets):
            if not isinstance(node.value, ast.List):
                raise LiteralError("QUESTIONS 가 목록 리터럴이 아님")
            out = []
            for el in node.value.elts:
                if not (isinstance(el, ast.Call) and getattr(el.func, "id", None) == "dict"):
                    raise LiteralError("QUESTIONS 원소가 dict(...) 가 아님")
                d = {}
                for kw in el.keywords:
                    if kw.arg in STRUCT_KEYS:  # 질문 문장(q) · 설명 칸은 꺼내지도 않는다
                        try:
                            d[kw.arg] = ast.literal_eval(kw.value)
                        except ValueError as e:
                            raise LiteralError(f"{d.get('id')}: {kw.arg} 가 리터럴이 아님") from e
                out.append(d)
            return out
    raise LiteralError("QUESTIONS 없음")


def dir_hash(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.glob("*.csv")):
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


async def name_to_entry(hub: HubRest, name: str, prefer: set[str]) -> tuple[Entry | None, dict]:
    """조각 전체가 이름과 같은 후보만 → 시작 표 먼저 → 하나면 확정 · 여럿이면 기록(사용자 확인 때 본다)."""
    res = await hub.resolve(name)
    cands = res.get("candidates") or []
    full = [c for c in cands if ((c.get("matched") or {}).get("span") or "").replace(" ", "") == name.replace(" ", "")]
    pool = full or cands
    concepts: dict[str, dict] = {}
    for c in pool:
        concepts.setdefault(f"{c['entity_table']}:{c['entity_code']}", c)
    main = [k for k, c in concepts.items() if c["entity_table"] in prefer and c["entity_table"] not in ATTR4]
    pick = main or [k for k, c in concepts.items() if c["entity_table"] in prefer] or list(concepts)
    note = {"name_kind": "full" if full else "partial", "concepts": list(concepts), "picked": pick[:1],
            "ambiguous": len(pick) > 1}
    if not pick:
        return None, note
    c = concepts[pick[0]]
    return Entry(c["entity_table"], c["entity_code"], c.get("entity_name") or name, "set"), note


PAIR_KIND_BY_RANGE = {"시술 조합": "combo", "동시 가능": "same_day", "순서·간격": "order_interval", "회피": "avoid"}
KIND_OF_TABLE = {"g_profile": ("overview", "mechanism"), "g_indication": ("indication",), "g_outcome": ("outcome",),
                 "g_adverse": ("adverse",), "g_adverse_detail": ("adverse",), "g_recovery": ("recovery",),
                 "g_contraindication": ("contraindication",), "g_offer": ("offer",)}


def expected_kinds(range_name_ko: str, gold_tables: str) -> tuple[list[str], list[str]]:
    """`gold_tables` 칸 → 정보 종류 초안(결정론) · 가르지 못한 것의 메모."""
    ks: list[str] = []
    notes: list[str] = []
    for t in [x.strip() for x in (gold_tables or "").split("·") if x.strip()]:
        if t == "g_entry":
            continue
        if t == "g_scope":
            if range_name_ko == "시술":
                ks.append("lineup")
            else:
                notes.append("g_scope(층 길 — 종류 없음)")
            continue
        if t == "g_pair":
            k = PAIR_KIND_BY_RANGE.get(range_name_ko)
            if k:
                ks.append(k)
            else:
                notes.append("g_pair(범위로 못 가름)")
            continue
        if t == "g_package":
            ks.append("same_day" if range_name_ko == "동시 가능" else "package")
            continue
        if t == "g_profile":
            notes.append("g_profile = 개요 · 원리 둘 다(B-3)")
        if t in KIND_OF_TABLE:
            ks.extend(KIND_OF_TABLE[t])
        else:
            notes.append(f"{t}(표 밖)")
    return [k for k in KIND_CODES if k in ks], notes


def narrow_by_entry(calls: list, by_node: dict[str, list[str]] | None) -> list:
    """대상별 기대 종류(`expected_kinds_by_entry` — B-1)가 있는 대상의 조회에서 그 대상의 종류 밖 종류를 뗀다 · 남는 종류가
    없으면 그 조회를 뺀다(예: 케이스의 `lineup` `scope`). 두 끝 조회(`b` 있음 — 짝 종류)는 건드리지 않는다. 없으면 그대로."""
    if not by_node:
        return calls
    out = []
    for c in calls:
        n = c.node()
        if n in by_node and not c.request.get("b"):
            ks = tuple(k for k in c.kinds if k in by_node[n])
            if not ks:
                continue
            c.kinds = ks
        out.append(c)
    return out


def spec_calls(entries: list[Entry], kinds: list[str], by_node: dict[str, list[str]] | None = None,
               groups: list[list[str]] | None = None) -> tuple[list[dict], list[dict], list[str]]:
    """B 와 같은 `plan_calls` — 읽기 호출 · (가) 판정용 호출(그 범위의 나머지) · 범위. `by_node` = 대상별 기대 종류(B-1 ·
    읽기 호출만 좁힌다 — 판정용은 그 범위의 나머지 조회 그대로라 대상에서 뗀 종류는 판정용으로 간다).
    `groups` = PR1 무리(B 와 같은 `walk.groups` 함수 — 세트 입구에는 `span` 이 없어 (ii) scope 로만 묶인다)."""
    calls = key_calls(narrow_by_entry(plan_calls(entries, kinds, groups=groups), by_node))
    attr = is_attr_start(e.table for e in entries)
    ranges = list(dict.fromkeys(r for e in entries for k in kinds for r in [range_of(k, e.table, attr)] if r))
    read = {c.key() for c in calls}
    judge = [c for c in key_calls(plan_calls(entries, range_kinds(entries, ranges), purpose="end_state", groups=groups))
             if c.key() not in read]
    return ([{**c.spec(), "kinds": list(c.kinds)} for c in calls],
            [{**c.spec(), "kinds": list(c.kinds)} for c in judge], ranges)


def _node_entry(x, names: dict[str, str], by: str = "set") -> Entry:
    if isinstance(x, str):
        t, c = x.split(":", 1)
        return Entry(t, c, names.get(x, ""), by)
    node = f"{x['table']}:{x.get('code', '')}"
    return Entry(x["table"], x.get("code", ""), x.get("name") or names.get(node, ""), by)


def _by_entry(item: dict, entries: list[Entry], extra: dict | None,
              kinds: list[str]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """세트 칸 `expected_kinds_by_entry`(B-1) → (명세에 적을 값 · 조회 대상별 종류). 키 전용 기준 대상은 `of` 대상을 따른다.
    칸이 없으면 둘 다 빈 dict — 명세가 지금과 바이트까지 같다."""
    raw = item.get("expected_kinds_by_entry") or {}
    if not raw:
        return {}, {}
    by_entry = {n: [k for k in KIND_CODES if k in ks] for n, ks in raw.items()}
    obj = {"expected_kinds": kinds, "expected_kinds_by_entry": by_entry}
    by_node = {e.node: kinds_of_entry(obj, e.node, (extra or {}).get(e.node, (None, None))[1]) for e in entries}
    return by_entry, by_node


async def build_spec(q: dict, hub: HubRest, qrow: dict | None = None, item: dict | None = None,
                     names: dict[str, str] | None = None) -> dict:
    """37 문항 명세. 답 키 고치기 1: 세트 항목(사용자 확정 칸)이 있으면 **그 값이 이긴다** —
    `expected_entries[]`(비면 gold-build 의 구조 값) · `expected_kinds[]`(비면 `gold_tables` 초안) ·
    `key_upper_entries[]`(`{node, of}` — 물은 고민에 행이 없고 윗고민에 있을 때 윗고민을 「윗층 기준」 대상으로 더한다 ·
    키 셈에서 `of` 대상의 `inherited` 로 센다 — Q10 · 허브 윗고민 물려받음 전 임시 · 코디 결정 (b))."""
    item = item or {}
    names = names or {}
    rdef = BY_NAME[q["range"]]
    prefer = start_tables([rdef.code])
    entries, notes = [], []
    if item.get("expected_entries"):
        entries = [_node_entry(x, names) for x in item["expected_entries"]]
        notes.append({"field": "set.expected_entries", "name_kind": "full", "concepts": [e.node for e in entries],
                      "picked": [e.node for e in entries], "ambiguous": False})
    else:
        for field in ("entry", "subject", "other", "adverse"):
            if q.get(field):
                e, note = await name_to_entry(hub, q[field], prefer)
                notes.append({"field": field, **note})
                if e:
                    entries.append(e)
        if q.get("offer_name"):
            entries.append(Entry("offer_name", "", q["offer_name"], "set"))
    extra: dict[str, tuple[str, str]] = {}  # node → (upper_of | lower_of, 물은 대상)
    for field, mark in (("key_upper_entries", "upper_of"), ("key_lower_entries", "lower_of")):
        for u in item.get(field) or []:
            e = _node_entry(u["node"], names, by=mark.split("_")[0])
            if e.node not in extra:
                entries.append(e)
                extra[e.node] = (mark, u["of"])
    dropped = {k: q[k] for k in NARROWING if k in q}
    fixed = PAIR_CLASSES.get({"sequence": "order_interval"}.get(rdef.code, rdef.code))
    excluded: dict = {}
    if q.get("classes") and (fixed is None or tuple(q["classes"]) != fixed):
        dropped["classes"] = q["classes"]
        if fixed is not None:
            out = [c for c in q["classes"] if c not in fixed]
            if out:
                excluded["classes"] = out  # 범위 표가 읽지 않는 갈래(BR3 — 키가 표본보다 좁아지는 자리)
    range_code = ATTR_START if is_attr_start(e.table for e in entries) else rdef.code  # R2-F1 — B 와 같은 판정
    kinds, knotes = expected_kinds(q["range"], (qrow or {}).get("gold_tables") or "")
    if item.get("expected_kinds"):
        kinds, knotes = [k for k in KIND_CODES if k in item["expected_kinds"]], []
    by_entry, by_node = _by_entry(item, entries, extra, kinds)
    if by_entry:
        kinds = [k for k in KIND_CODES if k in kinds or any(k in v for v in by_entry.values())]
    calls, judge, ranges = spec_calls(entries, kinds, by_node, await entry_groups(hub, entries))
    return {"id": q["id"], "range": q["range"], "range_code": range_code,
            "entries": [{"table": e.table, "code": e.code, "name": e.name,
                         **({extra[e.node][0]: extra[e.node][1]} if e.node in extra else {})} for e in entries],
            "entries_from": "set" if item.get("expected_entries") else "gold-build",
            "kinds_from": "set" if item.get("expected_kinds") else "gold_tables",
            "key_fix1": bool(item.get("key_fix1")),
            "expected_kinds": kinds, **({"expected_kinds_by_entry": by_entry} if by_entry else {}),
            "kinds_notes": knotes, "ranges": ranges,
            "calls": calls, "judge_calls": judge, "asked_fields": list(ASKED_TIME) if q.get("need_time") else [],
            "narrowing_dropped": dropped, "range_excluded": excluded, "resolve_notes": notes}


async def convert(s: Settings, gold_build: Path) -> dict:
    gold_dir = gold_build.parent
    before = dir_hash(gold_dir)
    qs = read_questions(gold_build)
    from clinic_chat.evalset.loader import read_questions_csv

    qcsv = read_questions_csv(s)
    hub = HubRest(s.hub_rest_url, page_limit=s.hub_page_limit)
    out_dir = set_dir(s) / "calls"
    out_dir.mkdir(parents=True, exist_ok=True)
    old = set_dir(s) / "_v041" / "calls"
    for p in sorted(out_dir.glob("*.json")):  # 옛 명세는 지우지 않고 옮긴다(WP-7 7)
        old.mkdir(parents=True, exist_ok=True)
        if not (old / p.name).exists():
            p.rename(old / p.name)
    sset = load_set(s)
    names = await _entry_names(hub)
    n = 0
    for q in qs:
        spec = await build_spec(q, hub, qcsv.get(q["id"]), sset.get(q["id"]), names)
        (out_dir / f"{q['id']}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        n += 1
    after = dir_hash(gold_dir)
    from clinic_chat.evalset.loader import fill_expected_entries

    return {"specs": n, "gold_hash_unchanged": before == after, "out": str(out_dir),
            "expected_entries": fill_expected_entries(s)}  # W-3 — 세트 칸을 명세 entries 와 같은 값으로


async def _entry_names(hub: HubRest) -> dict[str, str]:
    from clinic_chat.evalset.answer_key import ENTRY_TABLES

    ra = await hub.read_all("resolve", {"tables": list(ENTRY_TABLES)})
    if not ra.complete:
        raise RuntimeError(f"입구 목록을 끝까지 못 읽음: {ra.error}")
    out: dict[str, str] = {}
    for r in ra.items:
        out.setdefault(f"{r['entity_table']}:{r['entity_code']}", r.get("entity_name") or r.get("text") or "")
    return out


async def entry_groups(hub: HubRest | None, entries: list[Entry]) -> list[list[str]] | None:
    """PR1 무리 — 대상 둘 이상일 때만 `scope` 를 부른다(무리 판정용 — 명세 `calls[]` 에는 넣지 않는다 · `KEY_TOOLS` 그대로)."""
    ts = [e for e in entries if e.table in TARGET4]
    if hub is None or len(ts) < 2:
        return None
    links, _ = await scope_links(hub, [t.node for t in ts])
    gs, _ = group_targets(ts, links)
    return [[t.node for t in g] for g in gs]


def spec_from_item(item: dict, names: dict[str, str], groups: list[list[str]] | None = None) -> dict:
    """세트 항목(37 밖 · 확정) → 호출 명세. 입구 · 범위는 항목 값 그대로 — 고치지 않는다."""
    raw = item.get("entries") or item.get("expected_entries") or []
    entries = []
    for x in raw:
        if isinstance(x, str):
            t, c = x.split(":", 1)
            entries.append(Entry(t, c, names.get(x, ""), "set"))
        else:
            node = f"{x['table']}:{x.get('code', '')}"
            entries.append(Entry(x["table"], x.get("code", ""), x.get("name") or names.get(node, ""), "set"))
    rdef = BY_NAME.get(item.get("range") or "")
    kinds = [k for k in KIND_CODES if k in (item.get("expected_kinds") or [])]
    by_entry, by_node = _by_entry(item, entries, None, kinds)
    if by_entry:
        kinds = [k for k in KIND_CODES if k in kinds or any(k in v for v in by_entry.values())]
    judge: list[dict] = []
    ranges: list[str] = []
    if rdef is None or not entries:
        range_code = rdef.code if rdef else "out_of_scope"
        calls: list[dict] = []
    else:
        range_code = ATTR_START if is_attr_start(e.table for e in entries) else rdef.code
        calls, judge, ranges = spec_calls(entries, kinds, by_node, groups)
    return {"id": item["id"], "range": item.get("range"), "range_code": range_code, "source": item.get("source"),
            "entries": [{"table": e.table, "code": e.code, "name": e.name} for e in entries],
            "expected_kinds": kinds, **({"expected_kinds_by_entry": by_entry} if by_entry else {}),
            "kinds_notes": [], "ranges": ranges,
            "calls": calls, "judge_calls": judge, "asked_fields": list(item.get("asked_fields") or []),
            "narrowing_dropped": {}, "range_excluded": {}, "resolve_notes": []}


def _item_entries(item: dict, names: dict[str, str]) -> list[Entry]:
    return [_node_entry(x, names) for x in item.get("entries") or item.get("expected_entries") or []]


async def convert_set(s: Settings, ids: list[str] | None = None) -> dict:
    items = [x for x in load_set(s).values() if x.get("source") != "37" and (not ids or x["id"] in ids)]
    todo = [x for x in items if x.get("status") == "confirmed"]
    hub = HubRest(s.hub_rest_url, page_limit=s.hub_page_limit)
    names = await _entry_names(hub) if todo else {}
    out_dir = set_dir(s) / "calls"
    out_dir.mkdir(parents=True, exist_ok=True)
    no_entry = []
    for x in todo:
        spec = spec_from_item(x, names, await entry_groups(hub, _item_entries(x, names)))
        if not spec["entries"]:
            no_entry.append(x["id"])
        (out_dir / f"{x['id']}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"specs": len(todo), "skipped_draft": [x["id"] for x in items if x.get("status") != "confirmed"],
            "no_entries": no_entry, "out": str(out_dir)}


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.evalset.key_calls")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--from-gold-build")
    g.add_argument("--from-set", action="store_true")
    ap.add_argument("--ids")
    a = ap.parse_args()
    s = load_settings()
    if a.from_set:
        print(json.dumps(asyncio.run(convert_set(s, a.ids.split(",") if a.ids else None)), ensure_ascii=False))
        return
    print(json.dumps(asyncio.run(convert(s, Path(a.from_gold_build))), ensure_ascii=False))


if __name__ == "__main__":
    main()
