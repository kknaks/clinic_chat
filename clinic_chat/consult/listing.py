"""시술 리스트(SPEC-004 §6-1 PL1 ~ PL9 · U-13 (a) · 검수 N-4) — 앱 결정론 · LLM 답 · Jev 점수와 무관.

재료 = 이번 턴 `Walk.reads` 가운데 `concern_candidates` · `purpose == "read"` 응답(+ 후보별 profile 이어 조회의 계열 이름).
같은 고민의 조회가 둘 이상이면(부위로 거른 것 + 안 거른 것 — BR6) gid 합집합으로 한 리스트(처음 나온 순서).
`modality_only[]` · `unreached[]` 는 시술 행에 섞지 않고 따로 묶음 둘로 전부.
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
                 "_mcode": {}}
            concerns[code] = c
            order.append(code)
        inh = resp.get("inherited_from")
        if inh and not c["inherited_from"]:
            up = next((str(x.get("asked_concern_name") or "") for g in resp.get("groups") or []
                       for x in g.get("rows") or []), "")
            c["inherited_from"] = {"concern_code": inh.get("concern_code"), "concern_name": up,
                                   "steps": inh.get("steps")}
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
        out.append({"concern_code": code, "concern_name": c["concern_name"], "inherited_from": c["inherited_from"],
                    "modalities": mods, "modality_only": c["modality_only"], "unreached": c["unreached"],
                    "counts": {"procedures": len(c["_procs"]), "modalities": len(mods),
                               "modality_only": len(c["modality_only"]), "unreached": len(c["unreached"]),
                               "rows": len(c["_seen"])}})
    return {"concerns": out}


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
        for m in c["modalities"]:
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
