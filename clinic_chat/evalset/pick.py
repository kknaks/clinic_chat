"""추가 묶음 문항 고르기 재료(WORK-002 §8 6-c) — 결정론 · LLM 없음. 로컬 gold CSV · 원장 확인 목록만 읽는다(세트 작업 단계).

묶음마다 조건에 맞는 코드 후보 목록을 `EVAL_SET_DIR/pick-candidates.json`(gitignore)에 쓴다. 워커가 이 후보로 질문 문장 초안을
세트 파일에 `status: draft` 로 적고 → 사용자가 확정한다(C-2). 이름 없는 질문 묶음의 `resolve` 후보 0 확인은 허브에 묻는다.

    uv run python -m clinic_chat.evalset.pick
    uv run python -m clinic_chat.evalset.pick --paths     # 길 찾기 16(§9-3c) — 허브 `coverage` 로 꼴에 맞는 코드 후보
    uv run python -m clinic_chat.evalset.pick --holes     # 구멍 묶음 10(§9-3d · WORK-008 WP-4) — 허브 0.5.2 응답으로 꼴 후보

`--paths` 는 `EVAL_SET_DIR/pick-paths.json` 에 쓴다: (i) 자기 0 · 하위에 행(효과 · 어디에 쓰나(indication) · 상품) (iv) 물려받음만 ·
덮인 조각(다른 이름 안에 든 이름) · 한 글자 이름 · 같은 `norm` 두 표 · 종류 하나만(원리 · 효과 · 회복 · 하위 목록 · 상품).
"""

from __future__ import annotations

import collections
import csv
import itertools
import json
import re
from pathlib import Path

from clinic_chat.config import Settings, load_settings
from clinic_chat.evalset.loader import set_dir

CODE_RE = re.compile(r"\b[A-Z]{2,4}-\d{2,5}\b")
SPLIT = " · "


def _read(p: Path) -> list[dict]:
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def candidates(gold: Path, lck_path: Path | None, top: int = 8, exclude_lck: set[str] | None = None) -> dict:
    g = {p.stem: _read(p) for p in gold.glob("g_*.csv")}
    entry = g["g_entry"]
    name = {(r["entity_table"], r["entity_code"]): r["entity_name"] for r in entry}
    out: dict[str, list] = {}
    pairs = g["g_pair"]
    out["same_day_blank_complement"] = [
        {"from": f"{r['from_table']}:{r['from_code']}", "to": f"{r['to_table']}:{r['to_code']}", "gid": r["gid"]}
        for r in pairs if r["question_class"] == "보완" and r["same_day"] == "" and r["from_code"] and r["to_code"]][:top]
    by_conf = collections.defaultdict(list)
    for r in pairs:
        if r["conflict_id"]:
            by_conf[r["conflict_id"]].append(r["gid"])
    out["conflicting_pairs"] = [{"conflict_id": k, "gids": v} for k, v in list(by_conf.items())[:top]]
    tg = collections.defaultdict(set)
    for r in g["g_adverse"]:
        tg[r["adverse_code"]].add((r["target_table"], r["target_code"]))
    out["reverse_big_adverse"] = [{"code": f"adverse:{c}", "targets": len(t), "name": name.get(("adverse", c))}
                                  for c, t in sorted(tg.items(), key=lambda x: -len(x[1]))[:top]]
    by_norm = collections.defaultdict(set)
    for r in entry:
        by_norm[r["norm"]].add((r["entity_table"], r["entity_code"]))
    out["norm_overlap"] = [{"norm_text": next(e["text"] for e in entry if e["norm"] == n),
                            "concepts": sorted(f"{t}:{c}" for t, c in s)}
                           for n, s in by_norm.items() if len({t for t, _ in s}) > 1][:40]
    attr_hits = collections.Counter()
    for t, cols in {"g_profile": ("materials", "methods", "anatomy_name", "site_name"), "g_offer": ("materials", "sites")}.items():
        for r in g.get(t, []):
            for c in cols:
                for item in (r.get(c) or "").split(SPLIT):
                    if item:
                        attr_hits[item] += 1
    for table in ("material", "method", "anatomy", "site"):
        codes = [(c, n) for (t, c), n in name.items() if t == table]
        out[f"attr_{table}"] = [{"code": f"{table}:{c}", "name": n, "rows": attr_hits.get(n, 0)}
                                for c, n in sorted(codes, key=lambda x: -attr_hits.get(x[1], 0))[:top]]
    if lck_path and lck_path.is_file():
        lck = _read(lck_path)
        out["ledger_check_other"] = [{"id": r["id"], "codes": sorted(set(CODE_RE.findall(
            " ".join(r.get(c) or "" for c in ("silver_now", "what", "name")))))} for r in lck
            if r["id"] not in (exclude_lck or set()) and CODE_RE.search(" ".join(r.get(c) or "" for c in ("silver_now", "what", "name")))][:top * 3]
    out["products_with_parent"] = [{"subject": f"{r['subject_table']}:{r['subject_code']}", "holder":
                                    f"{r['holder_table']}:{r['holder_code']}", "role": r["role"]}
                                   for r in g["g_scope"] if r["role"] == "상위 제품"][:top]
    out["condition_targets"] = [{"code": f"condition:{c}", "name": name.get(("condition", c)), "rows": n}
                                for c, n in collections.Counter(r["condition_code"] for r in g["g_contraindication"]
                                                                if r["condition_code"]).most_common(top)]
    return out


async def path_candidates(gold: Path, hub, top: int = 6) -> dict:
    """§9-3c 길 찾기 꼴의 후보 — 셈은 허브 `coverage`(결정론)."""
    g = {p.stem: _read(p) for p in gold.glob("g_*.csv")}
    entry = g["g_entry"]
    name = {(r["entity_table"], r["entity_code"]): r["entity_name"] for r in entry}
    has_entry = {f"{t}:{c}" for t, c in name}
    lower_holders = sorted({f"{r['subject_table']}:{r['subject_code']}" for r in g["g_scope"]
                            if r["role"].startswith("하위") and f"{r['subject_table']}:{r['subject_code']}" in has_entry})
    targets = sorted(n for n in has_entry if n.split(":", 1)[0] in ("product", "procedure", "modality", "procedure_case"))
    cov = (await hub.coverage(targets))["items"]
    by = {it["node"]: {k["kind"]: k for k in it["kinds"]} for it in cov}

    def nm(n: str) -> str:
        t, c = n.split(":", 1)
        return name.get((t, c), "")
    out: dict[str, list] = {}

    def pick(key: str, cond, kinds: tuple[str, ...], pool=targets, prefix: str | None = None) -> None:
        got = []
        for n in pool:
            if prefix and not n.startswith(prefix):
                continue
            for k in kinds:
                c = by.get(n, {}).get(k)
                if c and cond(c):
                    got.append({"node": n, "name": nm(n), "kind": k, "self": c["self"], "lower": c["lower"],
                                "inherited": c["inherited"]})
        out[key] = got[:top]
    lower_only = lambda c: not c["self"] and (c["lower"] or 0) > 0  # noqa: E731
    pick("i_product_lower", lower_only, ("outcome", "indication"), lower_holders, "product:")
    pick("ii_procedure_lower", lower_only, ("outcome", "indication"), lower_holders, "procedure:")
    pick("iii_modality_offer_lower", lower_only, ("offer",), lower_holders, "modality:")
    pick("iv_inherited_only", lambda c: not c["self"] and not c["lower"] and (c["inherited"] or 0) > 0,
         ("outcome", "indication"), targets, "procedure_case:")
    pick("one_mechanism", lambda c: not c["self"] and not c["lower"] and (c["inherited"] or 0) > 0, ("mechanism",),
         targets, "product:")
    pick("one_outcome", lambda c: (c["self"] or 0) > 0, ("outcome",), targets, "procedure:")
    pick("one_recovery", lambda c: (c["self"] or 0) > 0, ("recovery",), targets, "procedure:")
    pick("one_lineup", lambda c: (c["lower"] or 0) > 1, ("lineup",), targets, "modality:")
    pick("one_offer", lambda c: (c["self"] or 0) > 0, ("offer",), targets, "product:")
    norms = sorted({r["norm"] for r in entry if r["norm"]}, key=len)
    tables: dict[str, set] = collections.defaultdict(set)
    for r in entry:
        tables[r["norm"]].add(f"{r['entity_table']}:{r['entity_code']}")
    long_names = [r for r in entry if len(r["norm"]) >= 5]
    covered = []
    for r in long_names:
        inner = [n for n in norms if 2 <= len(n) < len(r["norm"]) and n in r["norm"]]
        if inner and r["entity_table"] in ("product", "procedure"):
            covered.append({"long": r["text"], "long_id": f"{r['entity_table']}:{r['entity_code']}", "inner": inner[:3]})
    out["covered_pieces"] = covered[:top]
    out["one_char_names"] = [{"text": r["text"], "id": f"{r['entity_table']}:{r['entity_code']}"} for r in entry
                             if len(r["norm"]) == 1][:top * 3]
    out["same_norm_two_tables"] = [{"norm": n, "concepts": sorted(v)} for n, v in tables.items()
                                   if len({x.split(':')[0] for x in v}) > 1][:top * 3]
    return out


# ───────────── 구멍 묶음 셋(§9-3d · WORK-008 WP-4) — 셈은 허브 0.5.2 응답(결정론 · Jev · LLM 없음) ─────────────

HOLE_BUNDLES = {"HG1": "윗고민 물려받음", "HG2": "계열 아래 케이스", "HG3": "고민 + 부위"}
# 검수 §2 · SPEC §9-3d 「셈 근거」 · SPEC-001 §5-9 의 수 — 다르면 보고(코드는 맞추지 않는다)
HOLE_EXPECT = {"HG1_i": 9, "HG1_ii": 4, "HG1_iii": 41, "HG2_modalities": 18, "HG2_iii_spec": 0,
               "HG2_iii_b_pairs": 24, "HG2_iii_b_modalities": 7, "HG3_pairs": 97, "HG3_i": 37, "HG3_iii": 91}


def _ld(r: dict, side: str = "") -> int:
    lay = r.get(f"{side}layer") or {}
    d = lay.get("depth")
    return d if isinstance(d, int) else 1


async def _rows(hub, call) -> tuple[dict, list[dict]]:
    from clinic_chat.walk.verified import response_rows

    ra = await hub.read_all(call.tool, call.request)
    if not ra.complete:
        raise RuntimeError(f"{call.tool} {call.request} 끝까지 못 읽음: {ra.error}")
    resp = ra.as_response()
    return resp, response_rows(resp)


def concern_tree(silver: Path) -> tuple[dict[str, str], dict[str, str | None], list[dict]]:
    """silver `concern.csv`(이름 · parent_code) · `concern_site.csv`(묶임) — 보조 입력과 같은 파일(읽기만)."""
    cons = _read(silver / "concern.csv")
    name = {r["code"]: r["name_ko"] for r in cons}
    parent = {r["code"]: (r.get("parent_code") or None) for r in cons}
    return name, parent, _read(silver / "concern_site.csv")


def ancestors(code: str, parent: dict[str, str | None]) -> list[str]:
    out, seen = [], {code}
    p = parent.get(code)
    while p and p not in seen:
        out.append(p)
        seen.add(p)
        p = parent.get(p)
    return out


def descendants_or_self(code: str, parent: dict[str, str | None]) -> set[str]:
    return {c for c in parent if c == code or code in ancestors(c, parent)}


async def hole_candidates(gold: Path, silver: Path, hub, top: int = 6) -> dict:
    """§9-3d 열 꼴의 후보 · 꼴마다 수. 허브 응답만 센다(gold CSV 는 입구 이름 · 목록, silver 는 고민 나무 · 묶임)."""
    from clinic_chat.walk.calls import Call, Entry, plan_calls

    entry = _read(gold / "g_entry.csv")
    ename = {(r["entity_table"], r["entity_code"]): r["entity_name"] for r in entry}
    cname, parent, csi = concern_tree(silver)
    concerns = sorted(c for t, c in ename if t == "concern")
    modalities = sorted(c for t, c in ename if t == "modality")
    site_name = {c: n for (t, c), n in ename.items() if t == "site"}
    out: dict = {"counts": {}, "forms": {}}
    cnt, forms = out["counts"], out["forms"]

    # HG1 — 고민마다 concern_candidates(기본 upper=auto)의 머리
    hg1: dict[str, list] = {"HG1_i": [], "HG1_ii": [], "HG1_iii": []}
    for c in concerns:
        resp, rows = await _rows(hub, Call("concern_candidates", {"concern_code": c}))
        up = resp.get("inherited_from")
        counts = resp.get("counts") or {}
        rec = {"node": f"concern:{c}", "name": cname.get(c) or ename.get(("concern", c), ""), "rows": len(rows),
               "groups": resp.get("total"), "self_rows": counts.get("self_rows")}
        if up:
            rec["inherited_from"] = up
            hg1["HG1_i" if up.get("steps") == 1 else "HG1_ii"].append(rec)
        elif (counts.get("self_rows") or len(rows)) and not resp.get("total"):
            hg1["HG1_iii"].append(rec)
    for k, v in hg1.items():
        cnt[k] = len(v)
        forms[k] = sorted(v, key=lambda x: (-x["rows"], x["node"]))[:top]

    # HG2 — 계열의 하위 케이스(depth ≥ 2) 행
    def mod_entry(m: str) -> Entry:
        return Entry("modality", m, ename.get(("modality", m), ""), "set")
    by_kind: dict[str, dict[str, dict]] = {}
    for m in modalities:
        for k in ("indication", "outcome", "adverse", "recovery", "offer"):
            n = {"depth1": 0, "depth2": 0}
            for call in plan_calls([mod_entry(m)], [k]):
                if call.tool != "facets":
                    continue
                _, rows = await _rows(hub, call)
                for r in rows:
                    n["depth2" if _ld(r) >= 2 else "depth1"] += 1
            by_kind.setdefault(k, {})[m] = n
    cnt["HG2_modalities"] = len(modalities)

    def mod_rec(m: str, ks: tuple[str, ...]) -> dict:
        return {"node": f"modality:{m}", "name": ename.get(("modality", m), ""),
                **{k: by_kind[k][m] for k in ks}}
    i_ = [m for m in modalities if any(by_kind[k][m]["depth2"] for k in ("indication", "outcome"))]
    cnt["HG2_i"] = len(i_)
    forms["HG2_i"] = sorted((mod_rec(m, ("indication", "outcome")) for m in i_),
                            key=lambda x: -(x["indication"]["depth2"] + x["outcome"]["depth2"]))[:top]
    iii = [m for m in modalities if by_kind["offer"][m]["depth2"] and not by_kind["offer"][m]["depth1"]]
    cnt["HG2_iii_spec"] = len(iii)  # SPEC 꼴(자기 · 한 단에 상품 0 · 두 단 아래에만) — 기대 0(W-3)
    forms["HG2_iii_spec"] = [mod_rec(m, ("offer",)) for m in iii][:top]
    ia = [m for m in modalities if by_kind["adverse"][m]["depth2"] or by_kind["recovery"][m]["depth2"]]
    cnt["HG2_iii_a"] = len(ia)
    forms["HG2_iii_a"] = sorted((mod_rec(m, ("adverse", "recovery")) for m in ia),
                                key=lambda x: -(x["adverse"]["depth2"] + x["recovery"]["depth2"]))[:top]
    ib: dict[str, list[str]] = {}
    for m in modalities:
        resp, _ = await _rows(hub, Call("scope", {"node": f"modality:{m}"}))
        for h in resp.get("holders") or []:
            if _ld(h) >= 2 or (isinstance(h.get("depth"), int) and h["depth"] >= 2):
                if h.get("holder_table") == "product":
                    ib.setdefault(m, []).append(f"product:{h.get('holder_code')}")
    cnt["HG2_iii_b_pairs"] = sum(len(set(v)) for v in ib.values())
    cnt["HG2_iii_b_modalities"] = len(ib)
    forms["HG2_iii_b"] = sorted(({"node": f"modality:{m}", "name": ename.get(("modality", m), ""),
                                  "products": sorted(set(v))} for m, v in ib.items()),
                                key=lambda x: (-len(x["products"]), x["node"]))[:top]
    ii = []
    for a, b in itertools.combinations(modalities, 2):
        for k in ("same_day", "order_interval"):
            rows: list[dict] = []
            for call in plan_calls([mod_entry(a), mod_entry(b)], [k]):
                if call.tool == "pair" and call.request.get("b"):
                    rows.extend((await _rows(hub, call))[1])
            deep = [r for r in rows if _ld(r, "a_") >= 2 or _ld(r, "b_") >= 2]
            if rows and len(deep) == len(rows):
                ii.append({"a": f"modality:{a}", "b": f"modality:{b}", "kind": k, "rows": len(rows)})
    cnt["HG2_ii"] = len(ii)
    forms["HG2_ii"] = sorted(ii, key=lambda x: (-x["rows"], x["a"], x["b"]))[:top]

    # HG3 — (윗고민, 부위) 짝 · 묶인 고민 자신 + 부위 · 묶임 없는 짝
    bound = collections.defaultdict(set)  # site → 묶인 고민
    for r in csi:
        bound[r["site_code"]].add(r["concern_code"])
    pairs = sorted({(u, r["site_code"]) for r in csi for u in ancestors(r["concern_code"], parent)})
    cnt["HG3_pairs"] = len(pairs)
    h3: dict[str, list] = {"HG3_i": [], "HG3_ii": [], "HG3_iii": [], "HG3_iv": []}

    async def cc_site(c: str, s_code: str) -> dict:
        resp, rows = await _rows(hub, Call("concern_candidates", {"concern_code": c, "site": site_name.get(s_code, "")}))
        bsm = (resp.get("counts") or {}).get("by_site_match") or {}
        return {"concern": f"concern:{c}", "concern_name": cname.get(c, ""), "site": f"site:{s_code}",
                "site_name": site_name.get(s_code, ""), "rows": len(rows), "groups": resp.get("total"),
                "column": bsm.get("column", 0), "concern_site": bsm.get("concern_site", 0),
                "self_rows": (resp.get("counts") or {}).get("self_rows"),
                **({"inherited_from": resp["inherited_from"]} if resp.get("inherited_from") else {})}
    for u, st in pairs:
        if st not in site_name:
            continue
        rec = await cc_site(u, st)
        if not rec["column"] and rec["concern_site"]:
            h3["HG3_i"].append(rec)
        elif rec["column"] and rec["concern_site"]:
            h3["HG3_ii"].append(rec)
    for r in sorted(csi, key=lambda x: x["id"]):
        if r["site_code"] not in site_name:
            continue
        rec = await cc_site(r["concern_code"], r["site_code"])
        if rec["self_rows"]:  # §9-3d 「자기 행 1 이상인」 묶인 고민 자신(HG1 로 윗고민 행만 받은 고민은 뺀다 — 따로 셈)
            h3["HG3_iii"].append(rec)
        elif rec["rows"]:
            cnt["HG3_iii_inherited_only"] = cnt.get("HG3_iii_inherited_only", 0) + 1
    # (iv) 묶임 없는 (고민, 부위) — 묶임이 있는 고민 가운데 묶이지 않은 부위(자손 · 조상 묶임도 없는 짝) · 앞에서 top 짝만
    seen = 0
    for c in sorted({r["concern_code"] for r in csi}):
        fam = descendants_or_self(c, parent) | set(ancestors(c, parent))
        for st in sorted(site_name):
            if bound[st] & fam:
                continue
            rec = await cc_site(c, st)
            rec["expected_end_state"] = "근거 충족" if rec["rows"] else "후보 소진"
            h3["HG3_iv"].append(rec)
            seen += 1
            if seen >= top * 2:
                break
        if seen >= top * 2:
            break
    for k, v in h3.items():
        cnt[k] = len(v)
        forms[k] = sorted(v, key=lambda x: (-x["rows"], x["concern"], x["site"]))[:top]
    cnt["HG3_iv_note"] = "표본(전수 아님)"
    out["expect"] = HOLE_EXPECT
    out["diff"] = {k: {"got": cnt.get(k), "expect": v} for k, v in HOLE_EXPECT.items() if cnt.get(k) != v}
    out["drafts"] = hole_drafts(forms)
    return out


def _draft(i: int, hg: str, form: str, rng: str, entries: list[str], kinds: list[str], end: str, **kw) -> dict:
    return {"id": f"H{i:02d}", "bundle": HOLE_BUNDLES[hg], "source": "구멍", "form": form, "range": rng,
            "question": "", "expected_entries": entries, "expected_kinds": kinds, "expected_end_state": end,
            "status": "draft", **kw}


def hole_drafts(forms: dict) -> list[dict]:
    """§9-3d 열 문항의 **꼴만**(`question` 비움 — §11 B-3 기본값). 꼴마다 첫 후보(행이 가장 많은 것) — 고르는 것은 사용자."""
    def first(k: str) -> dict | None:
        return (forms.get(k) or [None])[0]
    out: list[dict] = []
    for k, form in (("HG1_i", "(i)"), ("HG1_ii", "(ii)"), ("HG1_iii", "(iii)")):
        c = first(k)
        out.append(_draft(len(out) + 1, "HG1", form, "부위·고민", [c["node"]] if c else [], ["indication"],
                          "근거 충족", **({"note": "윗고민 행이 섞이지 않는다(U-3 = (a))"} if k == "HG1_iii" else {})))
    c = first("HG2_i")
    out.append(_draft(len(out) + 1, "HG2", "(i)", "시술", [c["node"]] if c else [], ["indication", "outcome"], "근거 충족"))
    c = first("HG2_ii")
    out.append(_draft(len(out) + 1, "HG2", "(ii)", "동시 가능" if (c or {}).get("kind") == "same_day" else "순서·간격",
                      [c["a"], c["b"]] if c else [], [c["kind"]] if c else [], "근거 충족"))
    a, b = first("HG2_iii_a"), first("HG2_iii_b")
    out.append(_draft(len(out) + 1, "HG2", "(iii)", "", [], [], "근거 충족", options=[  # W-3 — 고르지 않는다
        {"form": "(iii-a) 계열 → 케이스의 부작용 · 회복", "range": "부작용", "expected_entries": [a["node"]] if a else [],
         "expected_kinds": ["adverse", "recovery"]},
        {"form": "(iii-b) 계열 → 시술 → 제품(두 단 아래 제품 행)", "range": "시술",
         "expected_entries": [b["node"]] if b else [], "expected_kinds": ["overview", "lineup"],
         "products": (b or {}).get("products", [])}]))
    for k, form in (("HG3_i", "(i)"), ("HG3_ii", "(ii)"), ("HG3_iii", "(iii)"), ("HG3_iv", "(iv)")):
        c = first(k)
        end = (c or {}).get("expected_end_state") or "근거 충족"
        out.append(_draft(len(out) + 1, "HG3", form, "부위·고민", [c["concern"], c["site"]] if c else [], ["indication"],
                          end))
    return out


def main() -> None:
    import argparse
    import asyncio

    ap = argparse.ArgumentParser(prog="clinic_chat.evalset.pick")
    ap.add_argument("--paths", action="store_true")
    ap.add_argument("--holes", action="store_true", help="구멍 묶음 10(§9-3d) 꼴 후보 → pick-holes.json")
    ap.add_argument("--silver-dir", help="silver CSV 폴더(기본 = GOLD_CSV_DIR 옆 silver/)")
    a = ap.parse_args()
    s: Settings = load_settings()
    if a.holes:
        from clinic_chat.clients.hub_rest import HubRest

        gold = s.path(s.gold_csv_dir)
        if not gold or not gold.is_dir():
            raise SystemExit("GOLD_CSV_DIR 없음")
        silver = Path(a.silver_dir) if a.silver_dir else gold.parent / "silver"
        out = asyncio.run(hole_candidates(gold, silver, HubRest(s.hub_rest_url, page_limit=s.hub_page_limit)))
        p = set_dir(s) / "pick-holes.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"counts": out["counts"], "diff": out["diff"]}, ensure_ascii=False))
        return
    if a.paths:
        from clinic_chat.clients.hub_rest import HubRest

        gold = s.path(s.gold_csv_dir)
        if not gold or not gold.is_dir():
            raise SystemExit("GOLD_CSV_DIR 없음")
        out = asyncio.run(path_candidates(gold, HubRest(s.hub_rest_url, page_limit=s.hub_page_limit)))
        p = set_dir(s) / "pick-paths.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({k: len(v) for k, v in out.items()}, ensure_ascii=False))
        return
    gold = s.path(s.gold_csv_dir)
    if not gold or not gold.is_dir():
        raise SystemExit("GOLD_CSV_DIR 없음")
    from clinic_chat.evalset.loader import read_set_lines

    used = {i for x in read_set_lines(s) if x.get("source") == "37" for i in x.get("ledger_diff") or []}
    out = candidates(gold, s.path(s.ledger_check_path), exclude_lck=used)  # 37 문항에 이미 걸린 LCK 는 뺀다
    p = set_dir(s) / "pick-candidates.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: len(v) for k, v in out.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
