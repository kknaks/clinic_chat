"""시술 리스트(SPEC-004 §6-1 PL1 ~ PL9 · U-13 (a) · 검수 N-4) — 앱 결정론 · LLM 답 · Jev 점수와 무관.

재료 = 이번 턴 `Walk.reads` 가운데 `concern_candidates` · `purpose == "read"` 응답(+ 후보별 profile 이어 조회의 계열 이름).
같은 고민의 조회가 둘 이상이면(부위로 거른 것 + 안 거른 것 — BR6) gid 합집합으로 한 리스트(처음 나온 순서).
`modality_only[]` · `unreached[]` 는 시술 행에 섞지 않고 따로 묶음 둘로 전부.

0.3(SPEC-006 §6 LS1 ~ LS8 · WORK-014 §12 · SPEC-001 0.7.2 SP · 코디 W-1 · W-9 (a)): 켠 상태 꼴(`codes=True`)에 더한다 —
`descended[]`(계열 묶음 · `modality_level` · `from_gids[]` · 시술 `descended`) · LS4-1(① `in_groups` 시술은 다시 안 그림
② `groups` 에 시술이 없는 계열만 내려온 시술 전부 ③ 그 밖은 `hidden.descended_n` 에 수만) · `hidden{unreached_n, names[],
descended_n, undescended}` · SP(`site{applied, prefer_rows, prefer_groups}` · 표지 시술 · 계열을 안정 정렬로 앞에). 앱은 응답의
`site_applied` · `site_prefer` · `n_site_prefer` 만 읽는다(묶음 = 고민마다 자기 값 · 한 고민의 응답이 섞이면 `prefer` 로 그리고
`filter` 응답의 행은 표지 행으로 센다 — 코디 W-1). 끈 상태 꼴은 0.1 글자 그대로.
"""

from __future__ import annotations

from clinic_chat.consult.material import CC_TOOL, _profiles


def _add(xs: list, vals) -> None:
    for v in vals if isinstance(vals, list) else [vals]:
        if v not in (None, "") and v not in xs:
            xs.append(v)


def build_list(reads, concern_names: dict[str, str] | None = None, *, codes: bool = False) -> dict | None:
    """장부 `list` 꼴 — `concern_candidates` 를 읽지 않았으면 None(PL8). `codes`(0.2 켠 상태만) = 계열 묶음에
    `modality_code`(후보 profile 행의 값 — 계열을 코드로 견주려고 · WORK-012 §12). 끈 상태는 0.1 글자 그대로."""
    names = concern_names or {}
    prof = _profiles(reads)
    concerns: dict[str, dict] = {}
    order: list[str] = []
    for r in reads:
        if r.call.tool != CC_TOOL or r.call.purpose != "read":
            continue
        code = str(r.call.request.get("concern_code") or "")
        resp = r.ra.as_response()
        c = concerns.get(code)
        if c is None:
            c = {"concern_code": code, "concern_name": names.get(code, ""), "inherited_from": None,
                 "_mods": {}, "_procs": {}, "_seen": set(), "modality_only": [], "_mo": {}, "unreached": [],
                 "_mcode": {}, "_applied": set(), "_desc": {}, "_undesc": 0, "_marked": set()}
            concerns[code] = c
            order.append(code)
        inh = resp.get("inherited_from")
        if inh and not c["inherited_from"]:
            up = next((str(x.get("asked_concern_name") or "") for g in resp.get("groups") or []
                       for x in g.get("rows") or []), "")
            c["inherited_from"] = {"concern_code": inh.get("concern_code"), "concern_name": up,
                                   "steps": inh.get("steps")}
        applied = resp.get("site_applied")
        c["_applied"].add(applied)
        if codes:  # 0.3 — 표지 행 gid(prefer 의 site_prefer 참 · filter 응답의 행) · 내려온 묶음
            for g in resp.get("groups") or []:
                for x in g.get("rows") or []:
                    if applied == "filter" or (applied == "prefer" and x.get("site_prefer")):
                        c["_marked"].add(str(x.get("gid")))
            for dd in resp.get("descended") or []:
                mc = str(dd.get("modality_code") or "")
                d0 = c["_desc"].setdefault(mc, {"modality_code": mc, "modality_name": dd.get("modality_name") or "",
                                                "unit": dd.get("unit"), "procs": {}, "from_gids": []})
                for pr in dd.get("procedures") or []:
                    d0["procs"].setdefault(str(pr.get("procedure_code") or ""), pr)
                for fr in dd.get("from") or []:
                    if fr.get("gid") and str(fr["gid"]) not in d0["from_gids"]:
                        d0["from_gids"].append(str(fr["gid"]))
            c["_undesc"] = max(c["_undesc"], ((resp.get("counts") or {}).get("descended") or {}).get("undescended") or 0)
        for g in resp.get("groups") or []:
            gids = [str(x.get("gid")) for x in g.get("rows") or [] if str(x.get("gid")) not in c["_seen"]]
            if not gids:
                continue
            c["_seen"].update(gids)
            pcode = str(g.get("procedure_code") or "")
            p = c["_procs"].get(pcode)
            if p is None:
                node = f"{g.get('candidate_table')}:{g.get('candidate_code')}"
                mod = next((str(x.get("modality_name")) for x in prof.get(node, [])
                            if f"{x.get('subject_table')}:{x.get('subject_code')}" == node and x.get("modality_name")), "")
                if codes and mod not in c["_mcode"]:
                    c["_mcode"][mod] = next((str(x.get("modality_code")) for x in prof.get(node, [])
                                             if f"{x.get('subject_table')}:{x.get('subject_code')}" == node
                                             and x.get("modality_code")), "")
                p = {"procedure_code": pcode, "procedure_name": g.get("procedure_name") or "", "reach": [],
                     "concern_match": [], "gids": []}
                c["_procs"][pcode] = p
                c["_mods"].setdefault(mod, []).append(p)
            _add(p["reach"], g.get("reach") or [])
            _add(p["concern_match"], g.get("concern_match") or [])
            p["gids"].extend(gids)
        for x in resp.get("modality_only") or []:
            gid = str(x.get("gid"))
            if gid in c["_seen"]:
                continue
            c["_seen"].add(gid)
            name = str(x.get("candidate_name") or "")
            m = c["_mo"].get(name)
            if m is None:
                m = c["_mo"][name] = {"modality_name": name, "gids": []}
                c["modality_only"].append(m)
            m["gids"].append(gid)
        for x in resp.get("unreached") or []:
            gid = str(x.get("gid"))
            if gid in c["_seen"]:
                continue
            c["_seen"].add(gid)
            c["unreached"].append({"candidate_name": x.get("candidate_name") or "", "reach": x.get("reach"), "gid": gid})
    if not order:
        return None
    out = []
    for code in order:
        c = concerns[code]
        mods = [{"modality_name": k, "procedures": v, **({"modality_code": c["_mcode"].get(k, "")} if codes else {})}
                for k, v in c["_mods"].items()]
        blk = {"concern_code": code, "concern_name": c["concern_name"], "inherited_from": c["inherited_from"],
               "modalities": mods, "modality_only": c["modality_only"], "unreached": c["unreached"],
               "counts": {"procedures": len(c["_procs"]), "modalities": len(mods),
                          "modality_only": len(c["modality_only"]), "unreached": len(c["unreached"]),
                          "rows": len(c["_seen"])}}
        if codes:
            _v3(blk, c, mods)
        out.append(blk)
    return {"concerns": out}


def _v3(blk: dict, c: dict, mods: list[dict]) -> None:
    """0.3 — 내려온 묶음(LS4 · LS4-1) · 숨긴 것(LS3 · LS5) · SP 표지 · 순서(SP-4)."""
    in_groups = set(c["_procs"])
    group_mods = {m.get("modality_code") for m in mods if m.get("modality_code") and m["procedures"]}
    desc, hidden_n = [], 0
    for d in c["_desc"].values():
        procs = [pr for pc, pr in d["procs"].items() if pc and pc not in in_groups and not pr.get("in_groups")]
        if d["modality_code"] in group_mods:  # LS4-1 ③ — 직접 닿은 시술이 있는 계열
            hidden_n += len(procs)
            continue
        if not procs:
            continue
        desc.append({"modality_code": d["modality_code"], "modality_name": d["modality_name"], "unit": d["unit"],
                     "modality_level": True, "from_gids": d["from_gids"],
                     "procedures": [{"procedure_code": str(pr.get("procedure_code") or ""),
                                     "procedure_name": pr.get("procedure_name") or "", "descended": True,
                                     "scope_gid": pr.get("scope_gid")} for pr in procs]})
    blk["descended"] = desc
    blk["hidden"] = {"unreached_n": len(c["unreached"]),
                     "names": list(dict.fromkeys(u["candidate_name"] for u in c["unreached"] if u["candidate_name"])),
                     "descended_n": hidden_n, "undescended": c["_undesc"]}
    applied = "prefer" if "prefer" in c["_applied"] else ("filter" if "filter" in c["_applied"] else None)
    blk["site"] = None
    if applied is None:
        return
    if applied == "filter":
        blk["site"] = {"applied": "filter", "prefer_rows": None, "prefer_groups": None}
        return
    marked = c["_marked"]
    n_groups = 0
    for m in mods:
        for p in m["procedures"]:
            p["site_prefer"] = bool(set(p["gids"]) & marked)
            n_groups += p["site_prefer"]
        m["site_prefer"] = any(p["site_prefer"] for p in m["procedures"])
        m["procedures"].sort(key=lambda p: not p["site_prefer"])  # SP-4 — 안정 정렬(그 밖은 허브 순서)
    mods.sort(key=lambda m: not m["site_prefer"])
    blk["site"] = {"applied": "prefer", "prefer_rows": len(marked & c["_seen"]), "prefer_groups": n_groups}


def state_list(obj: dict, focus_reads, names: dict[str, str], bundles: list[dict], syn_marks) -> dict:
    """0.2 켠 상태의 `list` 꼴(SPEC-005 SC2 · SC4 · WORK-012 §12 · 검수 §3-2 ①-a · ③): `{interest[], concerns[], synergy[]}`.

    ① 관심 대상 — `subjects[]`(거둔 것 빼고 · 고민이 없어도 그린다 · 닿는 시술) ② `focus` 고민의 리스트(0.1 `build_list` 그대로 +
    계열 코드 · 표지) — 고민 쪽 리스트가 묶음 ① 보다 먼저 보이는 자리는 템플릿 순서가 정한다 ③ 시너지(대상마다 · 안 · 밖).
    관심 표지(SC4 · 코디 W-3): `procedures[]` 의 시술 → 그 **시술 행** · 계열 대상 → 그 **계열 묶음 머리**(시술 행엔 안 단다)."""
    procs_of: dict[str, list[dict]] = {}
    for p in obj["procedures"]:
        procs_of.setdefault(p["via"]["subject"], []).append({"code": p["code"], "name": p["name"]})
    interest = [{"table": x["table"], "code": x["code"], "name": x.get("name") or "", "state": x["state"],
                 "procedures": procs_of.get(f"{x['table']}:{x['code']}", [])}
                for x in obj["subjects"] if x["state"] != "withdrawn"]
    lst = build_list(focus_reads, names, codes=True) if focus_reads else None
    concerns = (lst or {}).get("concerns") or []
    want = obj.get("focus")
    concerns = [c for c in concerns if c["concern_code"] == want] or concerns[:1]
    interest_procs = {p["code"] for p in obj["procedures"]}
    interest_mods = {x["code"] for x in obj["subjects"] if x["table"] == "modality" and x["state"] in ("said", "confirmed")}
    syn_procs, syn_mods = syn_marks
    for c in concerns:
        for m in [*c["modalities"], *(c.get("descended") or [])]:
            code = m.get("modality_code") or ""
            m["interest"] = bool(code and code in interest_mods)
            m["synergy"] = bool(code and code in syn_mods)
            m["caution"] = bool(code and syn_mods.get(code))
            for p in m["procedures"]:
                p["interest"] = p["procedure_code"] in interest_procs
                p["synergy"] = p["procedure_code"] in syn_procs
                p["caution"] = bool(syn_procs.get(p["procedure_code"]))
    return {"interest": interest, "concerns": concerns, "synergy": bundles}


def list_codes(lst: dict | None) -> tuple[set[str], set[str]]:
    """리스트 ② 의 시술 코드 · 계열 코드(SY5 의 「안」 판정)."""
    procs: set[str] = set()
    mods: set[str] = set()
    for c in (lst or {}).get("concerns") or []:
        for m in c.get("modalities") or []:
            if m.get("modality_code"):
                mods.add(m["modality_code"])
            for p in m.get("procedures") or []:
                procs.add(p["procedure_code"])
    return procs, mods


def view_codes(obj: dict) -> dict[str, str]:
    """0.3 FD7 · W-1 — 누를 수 있는 고민 코드 → 이름: 객체 축 고민 + 그 하위 고민(`concern_sites[]` 에 하위로 실린 코드)."""
    from clinic_chat.consult.sites import viewable_subs

    out = {x["code"]: x.get("name") or "" for x in obj.get("concerns") or [] if x.get("state") in ("said", "confirmed")}
    for k, v in viewable_subs(obj).items():
        out.setdefault(k, v)
    return out


def view_list(obj: dict, reads, codes: list[str], last: dict | None) -> dict:
    """0.3 FD5 — 누른 고민의 조각: 묶음 ② 만 그 고민 것(차례대로) · ① 관심 대상 · ③ 시너지는 마지막 리스트 그대로."""
    names = view_codes(obj)
    lst = build_list(reads, names, codes=True) or {"concerns": []}
    by = {c["concern_code"]: c for c in lst["concerns"]}
    concerns = [by[c] for c in codes if c in by]
    for c in concerns:
        c["concern_name"] = c["concern_name"] or names.get(c["concern_code"], "")
    return {"interest": (last or {}).get("interest") or [], "concerns": concerns, "synergy": (last or {}).get("synergy") or []}
