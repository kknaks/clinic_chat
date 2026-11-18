"""전체 답 키(WORK-002 §8 6-d ~ 6-g · SPEC-002 §9-5) — 호출 명세를 허브 REST 로 **끝까지** 넘겨 키 파일을 쓴다.

- LLM · Jev 를 부르지 않는다. 같은 명세 · 같은 build → 바이트까지 같은 키 파일.
- 키 파일에는 표 · `gid` · 코드 · 셈만 — 행의 값 칸은 복사하지 않는다. `g_scope` · `g_entry` 행은 `rows[]` 에 없다.
- BR3 「이 범위는 회피 갈래를 읽지 않는다」(순서·간격 — Q25): 명세의 `range_excluded.classes` 갈래의 표본은 키에 안 드는 것이
  기대다. 표본 대조 때만 그 갈래로 `pair` 를 따로 불러(키 행 · `calls[]` 에는 넣지 않는다) 안 든 표본이 그 갈래인지 가른다
  → `sample_check.out_of_range`. 그 밖의 안 든 표본만 「표본이 키에 안 듦」 으로 걸린다.
- 0.5.1: 명세 = 기대 대상 × 기대 종류(`key_calls.spec_calls` — B 의 `plan_calls`). 종류별 행도 RQ1 과 **같은 함수**로 거른다
  (N-2 — `overview` · `mechanism` 은 그 종류의 칸에 값이 있는 profile 행만 키에 든다 · 「칸 값으로 빠진 profile 행 수」 를 요약에).
  기대 종류마다 `self` · `lower` · `inherited` → `met_by`(`walk.required`) · (가) 는 판정용 호출(`judge_calls`)까지 같은 함수
  (`walk.end_state.unsettled_rows`) — 판정용 행은 키에 넣지 않는다. 옛(0.4.1) 키는 `_v041/answer-key/` 에 두고 행 수를 견준다.
- 답 키 고치기 1: `lineup`(K6) — 명세의 `scope` 호출 → `g_scope` 「하위 *」 행은 `rows[]` 밖 `lineup_rows[]`(gid · 코드만)와
  RQ1 셈(`lower`)에. 키 전용 기준 대상(`entries[].upper_of` — Q10 · `lower_of` — Q12)은 셈을 물은 대상의 `inherited` ·
  `lower` 로 접는다(`fold_extra`).
- 확인 전 키로는 채점하지 않는다(풀어 주는 플래그 · env 가 없다). `--confirm` 이 `confirmed{by, at}` 를 적는다.

    uv run python -m clinic_chat.evalset.answer_key --all
    uv run python -m clinic_chat.evalset.answer_key --confirm Q01,Q02 --by <확인한 사람>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.config import Settings, load_settings
from clinic_chat.evalset.loader import kinds_of_entry, load_set, read_questions_csv, set_dir
from clinic_chat.walk.calls import Call, followup_calls
from clinic_chat.walk.end_state import counted_rows, unsettled_rows
from clinic_chat.walk.keycodes import KEY_CODE, _codes, by_class, classify, value_side
from clinic_chat.walk.kinds import has_kind_value
from clinic_chat.walk.required import UPPER, call_side, count_rows, inherited_via, mark_upper, met_by
from clinic_chat.walk.verified import BUCKETS, _rows_in, table_of

ENTRY_TABLES = ("procedure", "procedure_case", "product", "modality", "concern", "effect", "adverse", "condition",
                "offer", "package", "material", "method", "anatomy", "site")

ROW_KEYS = ("gold_table", "gid", "bucket", "layer_role", "hier_match", "codes", "side_codes")
STATUS_MAP = {"답함": "근거 충족"}


def key_dir(s: Settings) -> Path:
    return set_dir(s) / "answer-key"


def script_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


async def entry_codes(hub: HubRest) -> set[str]:
    ra = await hub.read_all("resolve", {"tables": list(ENTRY_TABLES)})
    if not ra.complete:
        raise RuntimeError(f"입구 목록을 끝까지 못 읽음: {ra.error}")
    return {f"{r['entity_table']}:{r['entity_code']}" for r in ra.items}


def _keep(row: dict, kinds) -> bool:
    """N-2 — profile 행은 그 호출의 종류(개요 · 원리) 가운데 하나라도 칸 값이 있을 때만(부위·고민 후보별 profile 은 그대로)."""
    if table_of(row) != "g_profile" or not kinds or not set(kinds) & {"overview", "mechanism"}:
        return True
    return any(has_kind_value(row, k) for k in kinds if k in ("overview", "mechanism"))


async def read_rows(hub: HubRest, calls: list[dict], range_code: str = "", stats: dict | None = None
                    ) -> tuple[list[dict], list[dict], list, dict | None, list]:
    """명세 호출 + 결정론 이어 조회(부위·고민 후보별 profile)를 끝까지 → (키 행, 호출 기록, ledger_check, build, 원 행).

    `stats` 를 주면 `per_call[]`(호출마다 종류 · 대상 · 행) · `profile_dropped`(N-2 로 빠진 profile 행 수)를 채운다."""
    queue = [Call(c["tool"], c["request"], tuple(c.get("kinds") or ()), (range_code,)) for c in calls]
    done, recs, lc, rows, raw = set(), [], [], {}, []
    dropped_profile: set[str] = set()
    build = None
    while queue:
        c = queue.pop(0)
        if c.key() in done:
            continue
        done.add(c.key())
        ra = await hub.read_all(c.tool, c.request)
        if not ra.complete:
            raise RuntimeError(f"{c.tool} 끝까지 못 읽음: {ra.error}")
        if build is None:
            build = ra.build
        elif (ra.build or {}).get("gold_hash") != build.get("gold_hash"):
            raise RuntimeError("build 가 바뀜")
        recs.append({"tool": c.tool, "request": c.request, "total": ra.total})
        lc.extend(ra.ledger_check)
        resp = ra.as_response()
        if stats is not None:  # K6 — lineup 의 scope 는 holders(g_scope 행)가 RQ1 셈 재료다
            stats.setdefault("per_call", []).append({"call": c, "rows": mark_upper(resp, [  # HG1 — B 와 같은 셈
                r for b in BUCKETS if b != "holders" or c.tool == "scope" for r in _rows_in(resp.get(b), b)])})
            if c.tool == "scope":
                for r in _rows_in(resp.get("holders"), "holders"):
                    if str(r.get("role") or "").startswith("하위"):
                        stats.setdefault("lineup", {})[r["gid"]] = {
                            "gid": r["gid"], "role": r.get("role"),
                            "codes": _codes(r, [("subject_table", "subject_code")]),
                            "holder": _codes(r, [("holder_table", "holder_code")])}
        for b in BUCKETS:
            if b == "holders":
                continue
            bucket = "rows" if b == "rows" else b
            for row in mark_upper(resp, _rows_in(resp.get(b), b)):  # HG1 — (가) 셈 · B 와 같은 표지
                t = table_of(row)
                if t in ("g_scope", "g_entry") or t not in KEY_CODE:
                    continue
                if not c.meta.get("followup") and not _keep(row, c.kinds):
                    dropped_profile.add(row["gid"])
                    continue
                if row["gid"] not in rows:
                    rows[row["gid"]] = (bucket, row)
                    raw.append(row)
        for f in followup_calls(c, resp):
            queue.append(f)
    out = []
    for gid, (bucket, row) in rows.items():
        t = table_of(row)
        kc, sc = KEY_CODE[t]
        out.append({"gold_table": t, "gid": gid, "bucket": bucket, "layer_role": (row.get("layer") or {}).get("role"),
                    "hier_match": (row.get("hier") or {}).get("match"),
                    **({"inherited_from": row[UPPER]} if row.get(UPPER) else {}),
                    **({"layer_depth": _depth(row)} if _depth(row) >= 2 else {}),  # HG2 — 자손 층(대조 · 보고용)
                    "codes": _codes(row, kc),
                    "side_codes": _codes(row, sc) + _codes((row.get("layer") or {}).get("holder") or {},
                                                          [("holder_table", "holder_code")])})
    uniq_lc, seen = [], set()
    for x in lc:
        k = json.dumps(x, sort_keys=True, ensure_ascii=False)
        if k not in seen:
            seen.add(k)
            uniq_lc.append(x)
    uniq_lc.sort(key=lambda x: (0 if x.get("on") == "request" else 1, str(x.get("code"))))
    if stats is not None:
        stats["profile_dropped"] = len(dropped_profile - set(rows))
    return out, recs, uniq_lc, build, raw


def _depth(row: dict) -> int:
    """행의 자손 층 단 수(`layer` · `a_layer` · `b_layer` 가운데 큰 것 — 0.5.1 허브 행은 1)."""
    ds = [(row.get(k) or {}).get("depth") for k in ("layer", "a_layer", "b_layer")]
    return max([d for d in ds if isinstance(d, int)] or [1])


def key_classes(spec: dict, rows: list[dict], per_call: list[dict]) -> dict[str, str]:
    """DU2 — 키 행마다 갈래(입구 · 값 · 층). 입구 = 세트의 기대 대상(키 전용 기준 대상 `upper_of` · `lower_of` 는 뺀다) ·
    조회의 쪽(대상 쪽 / 값 쪽)은 그 행을 낸 조회의 시작 입구(`walk.keycodes.value_side` — B 의 이름 목록과 같은 함수)."""
    gids = {r["gid"] for r in rows}
    ents = {f"{e['table']}:{e['code']}" for e in spec.get("entries") or [] if not (e.get("upper_of") or e.get("lower_of"))}
    occ = [(r, value_side(pc["call"].request, pc["call"].meta)) for pc in per_call for r in pc["rows"]
           if r.get("gid") in gids and table_of(r) in KEY_CODE]
    return classify(occ, ents)


def kinds_met(spec: dict, per_call: list[dict]) -> list[dict]:
    """기대 종류 × 기대 대상 → RQ1(`walk.required` 의 같은 셈). 대상별 기대 종류(`expected_kinds_by_entry` — B-1)가 있으면
    그 대상은 그 종류만(키 전용 기준 대상은 `of` 대상을 따른다)."""
    out = []
    for e in spec.get("entries") or []:
        node = f"{e['table']}:{e['code']}"
        for k in kinds_of_entry(spec, node, e.get("upper_of") or e.get("lower_of")):
            rows, side, hit = [], "a", False
            for pc in per_call:
                c = pc["call"]
                if k not in c.kinds or c.meta.get("followup"):
                    continue
                sd = call_side(c.request, node)  # B 의 `required.target_rows` 와 같은 매칭(W-1)
                if sd is None:
                    continue
                side = sd
                hit = True
                rows.extend(pc["rows"])
            if not hit:
                continue
            cnt = count_rows(rows, k, side)
            via = inherited_via(rows)  # HG1 — 허브가 낸 `inherited_from`(키 전용 대상이 아니라)
            out.append({"target": node, "kind": k, **cnt, "met_by": met_by(cnt),
                        **({"inherited_via": via} if via else {}), **{m: e[m] for m in FOLD if e.get(m)}})
    return fold_extra(out)


FOLD = {"upper_of": ("inherited", "inherited_via"), "lower_of": ("lower", "lower_via")}


def fold_extra(items: list[dict]) -> list[dict]:
    """키 전용 기준 대상의 셈을 물은 대상 줄로 접는다 — 윗층 기준(`upper_of` · Q10 — 윗고민의 행은 물은 고민에게 물려받은
    값)은 `inherited` 로 · 하위 기준(`lower_of` · Q12 — 하위 시술과 그 아래 케이스 · 제품의 행)은 `lower` 로. 서로 다른
    gid 셈이 아니라 줄 셈의 합이다(겹친 gid 는 키 `rows[]` 에서 한 번만 · 이 수는 「채운 층」 판정용). 물은 대상 줄이 없으면 세운다."""
    out = [dict(i) for i in items if not any(i.get(m) for m in FOLD)]
    for u in items:
        mark = next((m for m in FOLD if u.get(m)), None)
        if mark is None:
            continue
        col, via = FOLD[mark]
        tgt = next((i for i in out if i["target"] == u[mark] and i["kind"] == u["kind"]), None)
        if tgt is None:
            tgt = {"target": u[mark], "kind": u["kind"], "self": 0, "lower": 0, "inherited": 0}
            out.append(tgt)
        tgt[col] += u["self"] + u["lower"] + u["inherited"]
        tgt.setdefault(via, []).append(u["target"])
        tgt["met_by"] = met_by(tgt)
    return out


async def judge_rows(hub: HubRest, spec: dict) -> list[dict]:
    """(가) 판정용 호출(그 범위의 나머지 조회 — B 와 같은 `plan_calls`) — 키 행에는 넣지 않는다."""
    out = []
    for c in spec.get("judge_calls") or []:
        ra = await hub.read_all(c["tool"], c["request"])
        if not ra.complete:
            raise RuntimeError(f"판정용 {c['tool']} 끝까지 못 읽음: {ra.error}")
        resp = ra.as_response()
        resp.pop("holders", None)
        for b in BUCKETS:
            out.extend(_rows_in(resp.get(b), b))
    return out


def expected_state(item: dict, qrow: dict | None, rows: list[dict], raw: list[dict], lc: list, asked: list[str],
                   judgement: list[dict] | None = None, met: list[dict] | None = None) -> tuple[str, str]:
    if item and item.get("source") != "37" and item.get("expected_end_state"):
        return item["expected_end_state"], "set"
    u = unsettled_rows(raw, lc, judgement)  # B 와 같은 함수(Q10 — 상위 펼침 행 제외 · 판정용 조회 포함)
    if u:
        return "원천 미확정", u
    if asked:
        by_t: dict[str, list[str]] = {}
        for f in asked:
            t, c = f.split(".", 1)
            by_t.setdefault(t, []).append(c)
        hit = False
        for t, cols in by_t.items():
            trs = [r for r in raw if table_of(r) == t]
            if trs and all(all((r.get(c) or "") == "" for c in cols) for r in trs):
                hit = True
        if hit:
            return "원천 미확정", "(다)"
    status = (qrow or {}).get("status") or item.get("status_csv")
    if status in STATUS_MAP and met and any(m["met_by"] is None for m in met):
        return "상태 안 맞음", "rq1"  # 「답함」 인데 세 층 모두 0 인 기대 종류 — 값을 지어내지 않는다
    if status in STATUS_MAP:
        return STATUS_MAP[status], "status"
    if item.get("expected_end_state"):
        return item["expected_end_state"], "set"
    return "상태 안 맞음", "status"


def build_key(qid: str, spec: dict, rows: list[dict], recs: list[dict], lc: list, build: dict | None,
              ecodes: set[str], qrow: dict | None, item: dict, raw: list[dict], calls_sha: str,
              excluded_gids: set[str] | None = None, judgement: list[dict] | None = None,
              met: list[dict] | None = None, profile_dropped: int = 0, lineup: dict | None = None,
              classes: dict[str, str] | None = None) -> dict:
    allc = []
    for r in rows:
        for c in r["codes"]:
            if c not in allc:
                allc.append(c)
    side = []
    for r in rows:
        for c in r["side_codes"]:
            if c not in side and c not in allc:
                side.append(c)
    must = [r["gid"] for r in rows if r["gold_table"] in ("g_contraindication", "g_pair")
            and _must(r["gid"], raw)]
    ptrs = [p.strip() for p in ((qrow or {}).get("gold_pointers") or "").split("·") if p.strip()]
    gids = {r["gid"] for r in rows}
    st, by = expected_state(item, qrow, rows, raw, lc, spec.get("asked_fields") or [], judgement, met)
    return {
        "id": qid, "build": {"gold_hash": (build or {}).get("gold_hash"), "aux_hash": (build or {}).get("aux_hash")},
        "script_sha256": script_sha(), "calls_sha256": calls_sha, "calls": recs, "rows": rows,
        "codes": [c for c in allc if c in ecodes], "unnamed_codes": [c for c in allc if c not in ecodes],
        **({"codes_by_class": by_class([c for c in allc if c in ecodes], classes)} if classes is not None else {}),
        "side_codes": side, "must_rows": must, "ledger_check": lc, "expected_end_state": st,
        "expected_end_state_by": by, "expected_kinds": spec.get("expected_kinds") or [],
        **({"expected_kinds_by_entry": spec["expected_kinds_by_entry"]} if spec.get("expected_kinds_by_entry") else {}),
        "kinds_met": met or [], "profile_dropped_by_kind_columns": profile_dropped,
        "lineup_rows": sorted((lineup or {}).values(), key=lambda x: x["gid"]),
        "judgement": {"calls": len(spec.get("judge_calls") or []), "rows": len(counted_rows(judgement or []))}
        if judgement is not None else None,
        "sample_check": {"pointers": len(ptrs), "in_key": sum(1 for p in ptrs if p in gids),
                         "missing": [p for p in ptrs if p not in gids and p not in (excluded_gids or set())],
                         "out_of_range": [p for p in ptrs if p not in gids and p in (excluded_gids or set())],
                         "range_excluded": spec.get("range_excluded") or {}},
        "confirmed": None,
    }


def _must(gid: str, raw: list[dict]) -> bool:
    r = next((x for x in raw if x.get("gid") == gid), {})
    return (table_of(r) == "g_contraindication" and r.get("polarity") == "금기") or \
           (table_of(r) == "g_pair" and r.get("question_class") == "회피")


async def excluded_gids(hub: HubRest, spec: dict) -> set[str]:
    """BR3 — 범위 표가 읽지 않는 갈래(`range_excluded.classes`)의 행 gid. 표본 대조에만 쓴다(키 행이 아니다)."""
    classes = (spec.get("range_excluded") or {}).get("classes")
    if not classes:
        return set()
    out: set[str] = set()
    for c in spec.get("calls") or []:
        if c["tool"] != "pair":
            continue
        req = {**c["request"], "classes": list(classes)}
        ra = await hub.read_all("pair", req)
        if not ra.complete:
            raise RuntimeError(f"표본 대조 pair 끝까지 못 읽음: {ra.error}")
        resp = ra.as_response()
        for b in BUCKETS:
            out |= {str(r.get("gid")) for r in _rows_in(resp.get(b), b)}
    return out


def dump(key: dict) -> str:
    return json.dumps(key, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def key_head(s: Settings, qid: str) -> dict | None:
    p = key_dir(s) / f"{qid}.json"
    if not p.is_file():
        return None
    b = p.read_bytes()
    k = json.loads(b)
    return {"sha256": hashlib.sha256(b).hexdigest(), "confirmed": bool(k.get("confirmed"))}


def load_key(s: Settings, qid: str) -> dict | None:
    p = key_dir(s) / f"{qid}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


async def make_all(s: Settings, ids: list[str] | None = None) -> dict:
    hub = HubRest(s.hub_rest_url, page_limit=s.hub_page_limit)
    ecodes = await entry_codes(hub)
    qcsv = read_questions_csv(s)
    sset = load_set(s)
    cdir = set_dir(s) / "calls"
    kd = key_dir(s)
    kd.mkdir(parents=True, exist_ok=True)
    made, summ = 0, []
    for p in sorted(cdir.glob("*.json")):
        spec = json.loads(p.read_text(encoding="utf-8"))
        qid = spec["id"]
        if ids and qid not in ids:
            continue
        st: dict = {}
        rows, recs, lc, build, raw = await read_rows(hub, spec["calls"], spec.get("range_code", ""), st)
        met = kinds_met(spec, st.get("per_call") or [])
        from clinic_chat.walk.end_state import needs_judgement

        jr = await judge_rows(hub, spec) if needs_judgement(raw, lc) else None
        if build is None:  # 호출 없는 명세(범위 밖 · 이름 없음 — W-8) — 키의 build 는 지금 허브 것
            build = (await hub.health()).get("build")
        item = sset.get(qid, {})
        key = build_key(qid, spec, rows, recs, lc, build, ecodes, qcsv.get(qid), item, raw,
                        hashlib.sha256(p.read_bytes()).hexdigest(), await excluded_gids(hub, spec), jr, met,
                        st.get("profile_dropped", 0), st.get("lineup"), key_classes(spec, rows, st.get("per_call") or []))
        old = load_key(s, qid)
        if old and old.get("confirmed") and {k: v for k, v in old.items() if k != "confirmed"} == \
                {k: v for k, v in key.items() if k != "confirmed"}:
            key["confirmed"] = old["confirmed"]
        (kd / f"{qid}.json").write_text(dump(key), encoding="utf-8")
        made += 1
        summ.append(summary_row(key, spec, qcsv.get(qid), old_rows(s, qid)))
    write_summary(s, summ)
    from clinic_chat.evalset.key_table import write_table

    write_table(s)
    flagged = [x for x in summ if x["flags"]]
    return {"keys": made, "flagged": len(flagged), "summary": str(set_dir(s) / "key-summary.md"),
            "sample_check_ok": sum(1 for x in summ if not x["missing"]), "gold_hash": summ[0]["gold_hash"] if summ else None}


def old_rows(s: Settings, qid: str) -> int | None:
    """0.4.1 키(`_v041/answer-key/`)의 행 수 — 「범위 길 → 대상 × 종류」 앞뒤 비교(WP-7 6)."""
    p = set_dir(s) / "_v041" / "answer-key" / f"{qid}.json"
    if not p.is_file():
        return None
    return len(json.loads(p.read_text(encoding="utf-8")).get("rows") or [])


def summary_row(key: dict, spec: dict, qrow: dict | None, old: int | None = None) -> dict:
    buckets: dict[str, int] = {}
    for r in key["rows"]:
        buckets[r["bucket"]] = buckets.get(r["bucket"], 0) + 1
    status = (qrow or {}).get("status")
    flags = []
    if key["sample_check"]["missing"]:
        flags.append("표본이 키에 안 듦")
    dropped = dict(spec.get("narrowing_dropped") or {})
    if (spec.get("range_excluded") or {}).get("classes"):
        flags.append("범위 표 갈래가 gold-build 보다 좁음(BR3 — 그 갈래 표본은 안 드는 것이 기대)")
    if {k for k in dropped if k != "classes"} or ("classes" in dropped and not (spec.get("range_excluded") or {})):
        flags.append("BR2 로 옮기지 않은 좁힘 — 키가 표본보다 넓을 수 있음")
    if key["expected_end_state"] == "상태 안 맞음" or (status == "답함" and key["expected_end_state"] != "근거 충족") \
            or (status and status != "답함" and key["expected_end_state_by"] == "status"):
        flags.append("상태 안 맞음")
    if not key["rows"]:
        flags.append("행 0")
    if not key["codes"] or len(key["unnamed_codes"]) > len(key["codes"]):
        flags.append("키 코드 0 또는 unnamed 대부분")
    if any(n.get("ambiguous") or n.get("name_kind") != "full" for n in spec.get("resolve_notes") or []):
        flags.append("입구 이름 맞춤 확인")
    if spec.get("kinds_notes"):
        flags.append("기대 종류 초안 확인(" + " · ".join(spec["kinds_notes"]) + ")")
    if any(e.get("upper_of") for e in spec.get("entries") or []):
        flags.append("윗층 기준 대상(키 전용 — 허브 윗고민 물려받음 전)")
    if any(e.get("lower_of") for e in spec.get("entries") or []):
        flags.append("하위 기준 대상(키 전용 — 허브 계열 → 손자 펼침 전)")
    if spec.get("key_fix1"):
        flags.append("답 키 검토 결정 반영(세트 칸)")
    if key.get("expected_end_state_by") == "rq1":
        flags.append("RQ1 로 상태가 바뀜")
    if old is not None and len(key["rows"]) < old:
        flags.append(f"0.4.1 키보다 줄어듦({old} → {len(key['rows'])})")
    return {"id": key["id"], "rows": len(key["rows"]), "buckets": buckets, "codes": len(key["codes"]),
            "unnamed": len(key["unnamed_codes"]), "missing": len(key["sample_check"]["missing"]),
            "out_of_range": len(key["sample_check"].get("out_of_range") or []),
            "pointers": key["sample_check"]["pointers"], "state": key["expected_end_state"],
            "by": key["expected_end_state_by"], "status": status, "lck": [i for x in key["ledger_check"] for i in x["lck_ids"]],
            "dropped": spec.get("narrowing_dropped"), "flags": flags, "gold_hash": key["build"]["gold_hash"],
            "confirmed": bool(key.get("confirmed")), "old_rows": old,
            "kinds": key.get("expected_kinds") or [], "profile_dropped": key.get("profile_dropped_by_kind_columns", 0),
            "met": " ".join(f"{m['kind']}={m['met_by']}" for m in key.get("kinds_met") or [])}


def write_summary(s: Settings, rows: list[dict]) -> None:
    lines = ["# 답 키 대조 요약(로컬 · gitignore)", "",
             f"gold_hash `{rows[0]['gold_hash'] if rows else ''}` · 키 {len(rows)} · 걸린 문항 {sum(1 for r in rows if r['flags'])}", "",
             "## 걸린 문항", "", "| id | 걸린 꼴 | 행 | 코드 | unnamed | 표본 안 든 수/표본(범위 갈래 밖 — 기대) | 기대 상태(길) | status | LCK | 옮기지 않은 좁힘 |",
             "|---|---|---:|---:|---:|---|---|---|---|---|"]
    def line(r: dict) -> str:
        return (f"| {r['id']} | {' · '.join(r['flags'])} | {r['rows']} | {r['codes']} | {r['unnamed']} | "
                f"{r['missing']}/{r['pointers']}({r['out_of_range']}) | {r['state']}({r['by']}) | {r['status']} | {' '.join(r['lck'])} | "
                f"{json.dumps(r['dropped'], ensure_ascii=False) if r['dropped'] else ''} |")
    lines += [line(r) for r in rows if r["flags"]]
    lines += ["", "BR3 — 범위 표의 고정 갈래가 gold-build 보다 좁으면 그 갈래 표본은 키에 안 든다(순서·간격 범위는 회피 갈래를"
              " 읽지 않는다). 「범위 갈래 밖」 수는 「표본 안 든 수」 에 넣지 않는다."]
    lines += ["", "## 전체", "", "| id | 기대 종류 | met_by | 행(0.4.1) | 칸 값으로 빠진 profile | bucket 별 | 코드 | unnamed | 표본 | "
              "기대 상태(길) | status | 확인 |",
              "|---|---|---|---:|---:|---|---:|---:|---|---|---|---|"]
    lines += [f"| {r['id']} | {' '.join(r['kinds'])} | {r['met']} | {r['rows']}({r['old_rows']}) | {r['profile_dropped']} | "
              f"{json.dumps(r['buckets'], ensure_ascii=False)} | {r['codes']} | {r['unnamed']} | "
              f"{r['pointers'] - r['missing'] - r['out_of_range']}/{r['pointers']} | {r['state']}({r['by']}) | {r['status']} | "
              f"{'✓' if r['confirmed'] else ''} |" for r in rows]
    p = set_dir(s) / "key-summary.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _key_pairs(k: dict) -> set[tuple[str, str]]:
    return {(r["gold_table"], r["gid"]) for r in k.get("rows") or []}


def _count(rows, f) -> dict:
    out: dict = {}
    for r in rows:
        v = f(r)
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda x: str(x[0])))


SAME_FIELDS = ("codes", "unnamed_codes", "side_codes", "kinds_met", "expected_end_state", "expected_end_state_by",
               "sample_check")  # 「같음」 = 행 집합 + 이 칸 전부(검수 v06 W-3 · 코디 W-3) — 명세는 `calls_sha256` 로 따로


def diff_keys(prev_dir: Path, now_dir: Path) -> list[dict]:
    """KF2 대조(WORK-008 §5 · 0.6 W-3) — 문항마다 (`gold_table`, `gid`) 집합 + `SAME_FIELDS` 앞뒤(`same` · 다른 칸
    `differs`) · 명세 해시(`calls_same`) · 표별 · bucket 별 · `kinds_met` · 기대 종료 · 갈래 수.
    `layer_role` 이 바뀐 것은 수로만 센다(달라도 된다 — 계열 기준 층으로 바뀜)."""
    out = []
    for p in sorted(now_dir.glob("*.json")):
        now = json.loads(p.read_text(encoding="utf-8"))
        pp = prev_dir / p.name
        prev = json.loads(pp.read_text(encoding="utf-8")) if pp.is_file() else None
        if prev is None:
            out.append({"id": now["id"], "prev": None})
            continue
        a, b = _key_pairs(prev), _key_pairs(now)
        fields = [f for f in SAME_FIELDS if prev.get(f) != now.get(f)]
        prow = {r["gid"]: r for r in prev.get("rows") or []}
        nrow = {r["gid"]: r for r in now.get("rows") or []}
        only_now = [nrow[g] for _, g in sorted(b - a)]
        both = [g for _, g in a & b]
        out.append({
            "id": now["id"], "same": a == b and not fields, "same_rows": a == b, "differs": fields,
            "calls_same": prev.get("calls_sha256") == now.get("calls_sha256"),
            "codes_by_class": {k: len(v) for k, v in (now.get("codes_by_class") or {}).items()},
            "prev_rows": len(a), "now_rows": len(b),
            "only_prev": len(a - b), "only_now": len(b - a),
            "only_prev_by_table": _count([prow[g] for _, g in a - b], lambda r: r["gold_table"]),
            "only_now_by_table": _count(only_now, lambda r: r["gold_table"]),
            "only_now_depth": _count(only_now, lambda r: r.get("layer_depth", 1)),
            "only_now_gids": sorted(r["gid"] for r in only_now)[:40],
            "only_prev_gids": sorted(g for _, g in a - b)[:40],
            "bucket_prev": _count(prow.values(), lambda r: r["bucket"]),
            "bucket_now": _count(nrow.values(), lambda r: r["bucket"]),
            "layer_role_changed": sum(1 for g in both if prow[g].get("layer_role") != nrow[g].get("layer_role")),
            "met_prev": " ".join(f"{m['target'].split(':')[-1]}.{m['kind']}={m['met_by']}"
                                 for m in prev.get("kinds_met") or []),
            "met_now": " ".join(f"{m['target'].split(':')[-1]}.{m['kind']}={m['met_by']}"
                                + (f"(via {','.join(v.split(':')[-1] for v in m['inherited_via'])})"
                                   if m.get("inherited_via") else "") for m in now.get("kinds_met") or []),
            "end_prev": f"{prev.get('expected_end_state')}({prev.get('expected_end_state_by')})",
            "end_now": f"{now.get('expected_end_state')}({now.get('expected_end_state_by')})",
            "sample_prev": f"{prev['sample_check']['in_key']}/{prev['sample_check']['pointers']}",
            "sample_now": f"{now['sample_check']['in_key']}/{now['sample_check']['pointers']}",
            "lck_now": sorted({i for x in now.get("ledger_check") or [] for i in x.get("lck_ids") or []}),
            "gold_hash": [prev["build"].get("gold_hash"), now["build"].get("gold_hash")],
            "aux_hash": [prev["build"].get("aux_hash"), now["build"].get("aux_hash")],
        })
    return out


def write_diff(s: Settings, prev: str, out_name: str = "key-diff-v052.md") -> dict:
    d = set_dir(s)
    pdir = Path(prev) if Path(prev).is_dir() else d / prev
    rows = diff_keys(pdir / "answer-key", key_dir(s))
    diff = [r for r in rows if "same" in r and not r["same"]]
    lines = [f"# 답 키 대조 — 앞 `{pdir.name}` ↔ 지금 `{d.name}`(로컬 · gitignore · WORK-008 §5 KF2 · 0.6 W-3)", "",
             f"문항 {len(rows)} · 같음(행 집합 + {' · '.join(SAME_FIELDS)}) {sum(1 for r in rows if r.get('same'))} · "
             f"다름 {len(diff)} · 명세(calls_sha256) 같음 {sum(1 for r in rows if r.get('calls_same'))}", "",
             "| id | 같음 | 다른 칸 | 명세 | 갈래(입구·값·층) | 행 앞→뒤 | 앞에만 | 뒤에만(표 · 단) | layer_role 바뀜 | bucket 앞 → 뒤 | "
             "kinds_met 앞 → 뒤 | 기대 종료 앞 → 뒤 | 표본 |",
             "|---|---|---|---|---|---|---:|---|---:|---|---|---|---|"]
    for r in rows:
        if "same" not in r:
            lines.append(f"| {r['id']} | (앞 키 없음) | | | | | | | | | | | |")
            continue
        cbc = r.get("codes_by_class") or {}
        lines.append(
            f"| {r['id']} | {'같음' if r['same'] else '**다름**'} | {' · '.join(r['differs'])} | "
            f"{'같음' if r['calls_same'] else '**다름**'} | "
            f"{cbc.get('entry', '')}·{cbc.get('value', '')}·{cbc.get('layer', '')} | "
            f"{r['prev_rows']}→{r['now_rows']} | {r['only_prev']} | "
            f"{r['only_now']} {json.dumps(r['only_now_by_table'], ensure_ascii=False)} "
            f"{json.dumps(r['only_now_depth'], ensure_ascii=False)} | {r['layer_role_changed']} | "
            f"{json.dumps(r['bucket_prev'], ensure_ascii=False)} → {json.dumps(r['bucket_now'], ensure_ascii=False)} | "
            f"{r['met_prev']} → {r['met_now']} | {r['end_prev']} → {r['end_now']} | {r['sample_prev']} → {r['sample_now']} |")
    tot = {k: sum((r.get("codes_by_class") or {}).get(k, 0) for r in rows) for k in ("entry", "value", "layer")}
    lines += ["", f"갈래 합(지금 키 `codes_by_class`): 입구 {tot['entry']} · 값 {tot['value']} · 층 {tot['layer']}"]
    hashes = {json.dumps(r["gold_hash"]) for r in rows if r.get("gold_hash")}
    aux = {json.dumps(r["aux_hash"]) for r in rows if r.get("aux_hash")}
    lines += ["", f"gold_hash 앞·뒤: {sorted(hashes)}", f"aux_hash 앞·뒤: {sorted(aux)}"]
    p = d / out_name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / (Path(out_name).stem + ".json")).write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n",
                                                     encoding="utf-8")
    return {"diff": str(p), "questions": len(rows), "differ": [r["id"] for r in diff],
            "calls_differ": [r["id"] for r in rows if "calls_same" in r and not r["calls_same"]], "classes": tot}


def confirm(s: Settings, ids: list[str], by: str) -> list[str]:
    done = []
    at = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
    for qid in ids:
        k = load_key(s, qid)
        if not k:
            continue
        k["confirmed"] = {"by": by, "at": at}
        (key_dir(s) / f"{qid}.json").write_text(dump(k), encoding="utf-8")
        done.append(qid)
    return done


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.evalset.answer_key")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--ids")
    ap.add_argument("--confirm")
    ap.add_argument("--by")
    ap.add_argument("--diff", help="앞 판 키 폴더(세트 폴더 아래 이름 또는 경로) — KF2 대조 → key-diff-v052.md")
    ap.add_argument("--diff-out", default="key-diff-v052.md")
    a = ap.parse_args()
    s = load_settings()
    if a.diff:
        print(json.dumps(write_diff(s, a.diff, a.diff_out), ensure_ascii=False))
        return
    if a.confirm:
        if not a.by:
            ap.error("--confirm 에는 --by 가 필요하다")
        print(json.dumps({"confirmed": confirm(s, a.confirm.split(","), a.by)}, ensure_ascii=False))
        return
    ids = a.ids.split(",") if a.ids else None
    if not (a.all or ids):
        ap.print_help()
        return
    print(json.dumps(asyncio.run(make_all(s, ids)), ensure_ascii=False))


if __name__ == "__main__":
    main()
