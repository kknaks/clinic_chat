"""단계 ④ — 앱 틀린 말 검사(SPEC-002 §4 · 답 전 K1 ~ K5 · G1 ~ G12 · + G13 · G14 — 답 키 고치기 1 K5). gate 는 **틀린 말 막기만** — 무엇을 더 붙이지 않는다.

실패면 종료 「실행 중단」(검사에 실패한 행은 단계 ⑤ 프롬프트에 한 행도 안 간다).

G13 = 효과 기간(outcome · 원장 `onset_duration`)을 부작용 발현 · 지속 시간으로 씀 · G14 = 다른 대상(하위 · 윗층)의 부작용 ·
회복 시간을 물은 대상 것처럼 씀 — G4 「층 섞기」 와 같은 꼴(표지 → K4 가 붙었나 확인 · 답 뒤 ① D11 이 잰다).
"""

from __future__ import annotations

import json
import re

from clinic_chat.walk.breadth import Read
from clinic_chat.walk.calls import Entry
from clinic_chat.walk.kinds import ATTR4, KIND_TABLES, TARGET4
from clinic_chat.walk.material import Material, time_kind, to_lines, unfold, upper_filled
from clinic_chat.walk.ranges import OFFER_NAME, is_attr_start, start_tables
from clinic_chat.walk.verified import Verified, layer_depth, layer_of, response_rows, site_match_by

GID_RE = re.compile(r"\bG[A-Z]{2}-\d{5}\b")
EXPLICIT = ("target", "uncertain_kept", "named", "noul")  # G7 대상(TG1 · TG2 · TG3 · ①-4 확정)


def _referenced(e: Entry, reads: list[Read]) -> bool:
    for r in reads:
        q = r.call.request
        if e.node in (q.get("node"), q.get("a"), q.get("b")) or (e.table == "concern" and e.code == q.get("concern_code")):
            return True
        if e.table == OFFER_NAME and q.get("offer_name_contains") == e.name:
            return True
        flt = q.get("filters") or {}
        if e.code in flt.values() or (e.table == "site" and (e.name in flt.values() or q.get("site") == e.name)):
            return True
    return False


def check(*, reads: list[Read], verified: Verified, entries: list[Entry], decisions: dict[str, dict],
          ranges: list[str], kinds: list[str], material: Material, planned: list, required: dict) -> dict:
    breadth = [r for r in reads if r.call.purpose == "read"]
    vg = set(verified.gids())
    k: dict[str, dict] = {}

    # K1 읽기(G9) — 조회 계획의 조회마다 read == total · complete · 계획에서 빠진 조회 0 · 읽은 gid ⊂ verified_gids ·
    #   줄 번호 수 = verified 수 · 줄 ↔ gid 빠짐 0 · 싣는 칸을 전부 실었나
    incomplete = [r.trace() for r in breadth if not r.ra.complete or (r.ra.total is not None and r.ra.read != r.ra.total)]
    done = {r.call.key() for r in reads}
    not_called = [c.key() for c in planned if c.key() not in done]
    union = {g for r in reads if r.call.purpose in ("read", "depth") for g in r.gids}
    missing = sorted(union - vg)
    lines_ok = len(material.lines) == len(vg) and {x["gid"] for x in material.lines} == vg \
        and [x["n"] for x in material.lines] == list(range(1, len(vg) + 1))
    flat = unfold(material.groups)
    cols_bad = [x["n"] for x in flat if set(material.expected_columns.get(x["n"], ())) - set(x)]
    k["K1"] = {"ok": not incomplete and not missing and not not_called and lines_ok and not cols_bad
               and len(flat) == len(vg),
               "incomplete": incomplete, "not_called": not_called[:20], "missing_gids": missing[:50],
               "missing_n": len(missing), "lines_ok": lines_ok, "columns_missing_lines": cols_bad[:50],
               "absent_columns": material.absent_columns}

    # K2 깊이 — 후보마다 결정 · deepen / gate_forced 로 정한 것을 다 읽었나
    read_cands = {r.call.candidate for r in reads if r.call.purpose == "depth"}
    undecided = [cid for cid, d in decisions.items() if d["decision"] == "uncertain" and d["closed_as"] is None]
    unread = [cid for cid, d in decisions.items()
              if (d["decision"] in ("deepen", "gate_forced") or d.get("closed_as") == "deepen") and cid not in read_cands]
    k["K2"] = {"ok": not undecided and not unread, "undecided": undecided, "unread": unread}

    # K3 물은 범위의 행 — G1 금기 · 회피 · G3 충돌 짝 · G7 명시 대상
    g1 = sorted({str(row.get("gid")) for r in breadth for row in response_rows(r.ra.as_response())
                 if (row.get("gold_table") == "g_contraindication" and row.get("polarity") == "금기")
                 or (row.get("gold_table") == "g_pair" and row.get("question_class") == "회피")})
    conflicts: dict[str, set[str]] = {}
    for r in breadth:
        for row in response_rows(r.ra.as_response()):
            if row.get("conflict_id"):
                conflicts.setdefault(str(row["conflict_id"]), set()).add(str(row.get("gid")))
    g3 = sorted({g for s in conflicts.values() for g in s})
    st = start_tables(ranges)
    attr_only = is_attr_start(e.table for e in entries)
    g7_missing, unused = [], []
    for e in entries:
        if _referenced(e, reads):
            continue
        if e.table == "site" and not attr_only:  # 부위는 BR1 필터 길이 있다 — 읽기로 한 종류의 조회에 `site` 필터 자리가 없을 때만
            unused.append({"id": e.node, "why": "br1_no_site_filter_on_read_kinds"})
        elif e.table in ATTR4 and not attr_only:  # W-2 · TG6 — 대상 · 값 입구와 함께인 속성 입구: BR1 에 그 필터 길이 없다
            unused.append({"id": e.node, "why": "br1_no_attr_filter"})
        elif e.table not in st and e.table not in TARGET4:  # W-7 · TG6 — 읽는 범위의 시작 표 밖 입구
            unused.append({"id": e.node, "why": "range_start_tables"})
        elif e.by in EXPLICIT or e.table == OFFER_NAME:
            g7_missing.append(e.node)
        else:
            unused.append({"id": e.node, "why": "no_query"})
    g7 = sorted({g for r in reads for g in r.gids if any(e.node == r.call.node() for e in entries)})
    k["K3"] = {"ok": set(g1) <= vg and set(g3) <= vg and not g7_missing,
               "g1_missing": sorted(set(g1) - vg), "g3_missing": sorted(set(g3) - vg), "g7_missing": g7_missing}

    # K4 표지 — 층 · polarity · same_day 빈칸 · 충돌 · basis · 케이스의 말할 이름 · holder · 물려받음 채움 · price_mid_raw 없음
    text = to_lines(material.groups)
    by_n = {x["n"]: x for x in flat}
    bad = []
    inherited_kinds = {i["kind"] for i in required.get("items", []) if i.get("met_by") == "inherited"}
    for line in material.lines:
        v = verified.rows[line["gid"]]
        m = by_n.get(line["n"], {}).get("marks", {})
        need = ["basis"]
        lay = layer_of(v.row)
        if lay:
            need.append("layer")
            if lay.get("role") != "자기" and (lay.get("holder") or {}).get("holder_name"):
                need.append("holder")
            if layer_depth(v.row) >= 2:  # HG2 — 거친 층 표지(이름 `layer_via` 는 얻었을 때만 · K4 는 단 수만 본다 — A-3)
                need.append("layer_depth")
        if upper_filled(v, inherited_kinds):  # G12 (ii) — 물려받음 층 · 윗고민 물려받음(HG1)
            need.append("inherited_fill")
        if site_match_by(v.row):  # HG3
            need.append("site_match")
        if v.gold_table == "g_pair" and (v.row.get("same_day") or "") == "":
            need.append("same_day_blank")
        if v.row.get("conflict_id"):
            need.append("conflict_lines")
        if any(v.row.get(c) == "procedure_case" for c in ("candidate_table", "target_table", "subject_table",
                                                           "from_table")):
            need.append("speak_name")
        if (v.row.get("hier") or {}).get("match"):
            need.append("hier")
        if (v.row.get("attr") or {}).get("column"):
            need.append("attr")
        for mk in ("narrowed", "value_pick", "same_day_package", "same_day_layer"):  # 0.6 표지 셋(+ SD1 층)
            if v.marks.get(mk):
                need.append(mk)
        if time_kind(v):  # G13 · G14 — 시간 칸이 있는 효과 · 부작용 · 회복 행은 「무엇의 시간」 · 남의 것이면 그 대상
            need.append("time_of")
            if v.gold_table != "g_outcome" and lay and lay.get("role") not in (None, "", "자기"):
                need.append("about")
        lack = [x for x in need if x not in m]
        if lack:
            bad.append({"n": line["n"], "lack": lack})
    leak = {"price_mid_raw": "price_mid_raw" in text, "gid": bool(GID_RE.search(text)),
            "silver_": "silver_" in text, "evidence_kind": "evidence_kind" in text}
    k["K4"] = {"ok": not bad and not any(leak.values()), "bad": bad[:50], "bad_n": len(bad), "leak": leak}

    # K5 붙이지 않음 — 행의 표 ∈ 읽기로 한 종류가 허용한 표(깊이 행은 그 깊이 조회의 종류) · 행의 종류 ⊂ 읽는 종류(+ 깊이)
    depth_kinds = {kk for r in reads if r.call.purpose == "depth" for kk in r.call.kinds}
    allowed_kinds = set(kinds) | depth_kinds
    outside = sorted({v.gid for v in verified.rows.values()
                      if not set(v.kinds) <= allowed_kinds
                      or v.gold_table not in {t for kk in v.kinds for t in KIND_TABLES.get(kk, ())}})
    k["K5"] = {"ok": not outside, "outside": outside[:50]}

    # G12 — 하위에 자기 행이 있는 종류를 물려받은 행만으로 채우지 않았나(RQ1 이 막음) · 물려받음 채움에 표지가 붙었나
    g12_bad = [i for i in required.get("items", []) if i.get("met_by") == "inherited" and i.get("lower")]
    g12 = {"id": "G12", "result": "pass" if not g12_bad and not any("inherited_fill" in b["lack"] for b in bad) else "fail",
           "inherited_kinds": sorted(inherited_kinds)}

    gates = [
        {"id": "G1", "result": "pass" if set(g1) <= vg else "fail", "forced_gids": g1},
        {"id": "G2", "result": "marked"}, {"id": "G3", "result": "pass" if set(g3) <= vg else "fail", "forced_gids": g3},
        {"id": "G4", "result": "marked"}, {"id": "G5", "result": "marked"},
        {"id": "G6", "result": "pass" if not leak["price_mid_raw"] else "fail"},
        {"id": "G7", "result": "pass" if not g7_missing else "fail", "forced_gids": g7, "missing": g7_missing},
        {"id": "G8", "result": "marked"},
        {"id": "G9", "result": "pass" if k["K1"]["ok"] else "fail", "read_eq_total": not incomplete,
         "gids_subset": not missing},
        {"id": "G10", "result": "marked"}, {"id": "G11", "result": "prompt_only"}, g12,
        {"id": "G13", "result": "marked"}, {"id": "G14", "result": "marked"},
    ]
    fails = [name for name, v in k.items() if not v["ok"]]
    return {"ok": not fails, "fails": fails, "K": k, "gates": gates, "unused_entries": unused}


def canon(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)
