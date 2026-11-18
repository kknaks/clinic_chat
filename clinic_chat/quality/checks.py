"""① 결정론 검사 D1 ~ D10 · D11(SPEC-002 §8-2 · D11 = 답 키 고치기 1 K5) — 코드 · LLM 없음 · **세 열에 같은 검사기 함수**.

0.5.1: 답은 id 를 줄마다 넣지 않는다(P2) — D1 · D2 는 답이 글자로 적은 id 가 없으면 「해당 없음」.
B 의 D2 = 답 꼬리의 줄 번호(`answer_lines[]`) ⊂ `verified_lines[]` 의 `n`. D10 「읽지 않은 것」(B · C — A 는 「해당 없음」) =
답의 이름 → 코드(D3) 가운데 그 열이 읽은 행의 키 코드 칸 · 곁 칸 어디에도 없는 것(기록 · 위반 수 아님).

열 이름으로 갈라지는 곳은 D2 의 재료 · D3 ② · D6 · D7 뿐이다(A 의 D3 ② · D7 = 「해당 없음」 — 0 으로 적지 않는다).
"""

from __future__ import annotations

import re
from typing import Any

from clinic_chat.columns.base import RAW_CITE_RE
from clinic_chat.quality.gold_codes import CODE_RE, GoldCodes, bare_code, row_spans, text_spans

NA = "해당 없음"
NUM_RE = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)(?![\w-])")
CITE_MARK_RE = re.compile(r"\[근거 \d+\]|\[\s*G[A-Z]{2}-\d{5}(?:\s*[,·]\s*G[A-Z]{2}-\d{5})*\s*\]")
LIST_NO_RE = re.compile(r"(?m)^\s*\d+[.)](?=\s)")
NUM_COLS_RE = re.compile(r"(_min|_max|^min$|^max$|^price_low$|^price_high$)")
CASE_NAME_COLS = ("candidate_name", "target_name", "name_ko")


def _num(x: str) -> str:
    return str(float(x)).rstrip("0").rstrip(".") if x else x


def d2_a(cited: list[str], tool_calls: list[dict]) -> list[str]:
    """A — 인용 포인터 ⊂ 돌려준 경로 자신 또는 하위(잘린 raw_get 은 자신만)."""
    bad = []
    for p in cited:
        ok = False
        for c in tool_calls:
            for q in c.get("paths") or []:
                if p == q:
                    ok = True
                elif not (c.get("tool") == "raw_get" and c.get("truncated")) and p.startswith(q) and p[len(q):][:1] in (".", "["):
                    ok = True
        if not ok:
            bad.append(p)
    return bad


def read_gids(column: str, trace: dict) -> list[str] | None:
    if column in ("b", "b_on"):
        return [x["gid"] for x in trace.get("verified_gids") or []]
    if column == "c":
        return [g for c in trace.get("tool_calls") or [] for g in c.get("gids") or []]
    return None


def run(*, column: str, answer: str, cited: list[str], trace: dict, key: dict, key_rows: dict[str, dict],
        gold: GoldCodes, raw_exists=None, end_state: str | None = None, expected_end_state: str | None = None,
        named_cases: set[str] | None = None) -> dict:
    """한 열 · 한 문항. `key_rows` = 행 값 읽기(같은 build 로 다시 부른 행 · gid → 행)."""
    col_a = column == "a"
    out: dict[str, Any] = {}
    # D1 인용 실재(답이 id 를 글자로 적지 않았으면 해당 없음)
    lines_b = trace.get("answer_lines") if column in ("b", "b_on") else None
    if not cited and lines_b is None:
        out["D1"] = NA
    elif not cited:
        out["D1"] = {"violations": []}
    elif col_a:
        out["D1"] = {"violations": [p for p in cited if not (raw_exists and raw_exists(p))]}
    else:
        out["D1"] = {"violations": [g for g in cited if g not in gold.gids]}
    # D2 인용 범위 — B 는 답 꼬리 줄 번호 ⊂ verified_lines.n(+ 글자로 적은 gid 가 있으면 ⊂ verified_gids)
    if column in ("b", "b_on") and lines_b is not None:
        ns = {x.get("n") for x in trace.get("verified_lines") or []}
        rg = set(read_gids(column, trace) or [])
        out["D2"] = {"violations": [*sorted({n for a in lines_b for n in a.get("rows") or [] if n not in ns}),
                                    *[g for g in cited if g not in rg]], "answer_lines": len(lines_b)}
    elif not cited:
        out["D2"] = NA
    elif col_a:
        out["D2"] = {"violations": d2_a(cited, trace.get("tool_calls") or [])}
    else:
        rg = set(read_gids(column, trace) or [])
        out["D2"] = {"violations": [g for g in cited if g not in rg]}
    # D3 코드
    found = gold.find_names(answer)
    kc = set(key.get("codes") or [])
    concern_form = any(r["bucket"] == "groups" for r in key.get("rows") or [])
    cbc = key.get("codes_by_class")  # 0.6 DU1 — 값 코드가 주 지표 · 층은 따로 · 입구는 뺀다(옛 키는 지금 식 그대로)
    layer: set[str] | None = None
    if cbc is not None:
        kc = set(cbc.get("value") or [])
        layer = set(cbc.get("layer") or [])
    if concern_form:  # 케이스 후보는 procedure:procedure_code 로 센다(Y-2)
        kc = {c for c in kc if not c.startswith("procedure_case:")}
        layer = {c for c in layer if not c.startswith("procedure_case:")} if layer is not None else None
    missing = sorted(kc - found)
    invented = None if col_a else sorted({m for m in CODE_RE.findall(answer)
                                          if not any(bare_code(c) == m for c in gold.codes)})
    extra = sorted(found - set(key.get("codes") or []))
    out["D3"] = {"missing": missing, "missing_ratio": (len(missing) / len(kc)) if kc else None, "key_codes": len(kc),
                 "invented": invented if invented is not None else NA, "extra_codes": extra}
    if layer is not None:
        out["D3"].update({"by_class": True, "na": NA if not kc else None,  # 값 코드 0 문항 = D3 ① 해당 없음
                          "layer_missing": sorted(layer - found), "layer_codes": len(layer)})
    if concern_form:
        cands = set()
        for r in key.get("rows") or []:
            if r["bucket"] != "groups":
                continue
            row = key_rows.get(r["gid"]) or {}
            if row.get("candidate_table") == "procedure_case":
                if row.get("procedure_code"):
                    cands.add(f"procedure:{row['procedure_code']}")
            elif row.get("candidate_table"):
                cands.add(f"{row['candidate_table']}:{row['candidate_code']}")
        out["M1"] = {"group_candidates": len(cands), "missing": len(cands - found)}
    # D4 숫자
    nums = set()
    for row in key_rows.values():
        for c, v in row.items():
            if NUM_COLS_RE.search(c) and v not in (None, ""):
                nums.add(_num(str(v)))
    # W-5 — 인용 꼴 전체(원장 포인터 · [gid] · [근거 n]) · 줄 머리 목록 번호를 먼저 지운다(세 열 같은 식)
    masked = LIST_NO_RE.sub(" ", RAW_CITE_RE.sub(" ", CITE_MARK_RE.sub(" ", answer)))
    masked = CODE_RE.sub(" ", re.sub(r"\bG[A-Z]{2}-\d{5}\b", " ", masked))
    out["D4"] = {"violations": sorted({n for n in NUM_RE.findall(masked) if _num(n) not in nums})}
    # D5 gate 위반 — G1 · G5 · G6
    must_codes = set()
    for g in key.get("must_rows") or []:
        row = key_rows.get(g) or {}
        for t, c in (("target_table", "target_code"), ("from_table", "from_code"), ("to_table", "to_code")):
            if row.get(t) and row.get(c):
                must_codes.add(f"{row[t]}:{row[c]}")
        if row.get("condition_code"):
            must_codes.add(f"condition:{row['condition_code']}")
    g5 = sorted({f"adverse:{r['adverse_code']}" for r in key_rows.values() if r.get("polarity") == "해당 없음"
                 and r.get("adverse_code") and f"adverse:{r['adverse_code']}" in found})
    g6 = sorted({str(r["price_mid_raw"]) for r in key_rows.values() if r.get("price_mid_raw")
                 and str(r["price_mid_raw"]) in answer})
    out["D5"] = {"G1_missing": sorted(must_codes - found), "G5": g5, "G6": g6,
                 "violations": len(must_codes - found) + len(g5) + len(g6)}
    # D6 앱 판정(B 전용)
    out["D6"] = {"match": end_state == expected_end_state} if column == "b" else NA
    # D7 폭 누락(B · C)
    rg = read_gids(column, trace)
    if rg is None:
        out["D7"] = NA
    else:
        rgs = set(rg)
        rows = key.get("rows") or []
        missed = [r["gid"] for r in rows if r["gid"] not in rgs]
        read_codes = {c for r in rows if r["gid"] in rgs for c in r["codes"]}
        mc = [c for c in key.get("codes") or [] if c not in read_codes]
        out["D7"] = {"missed_rows": len(missed), "key_rows": len(rows),
                     "missed_rows_ratio": (len(missed) / len(rows)) if rows else None,
                     "missed_codes": len(mc), "key_codes": len(key.get("codes") or []),
                     "missed_must_rows": len(set(missed) & set(key.get("must_rows") or []))}
    # D8 묻지 않은 것(기록만)
    key_tables = {c.split(":", 1)[0] for c in key.get("codes") or []}
    un = {"safety": [], "offer": [], "effect": []}
    for c in extra:
        t = c.split(":", 1)[0]
        if t in ("adverse", "condition") and t not in key_tables:  # N-7 — 표마다 따로
            un["safety"].append(c)
        elif t == "offer" and "offer" not in key_tables:
            un["offer"].append(c)
        elif t == "effect" and "effect" not in key_tables:
            un["effect"].append(c)
    out["D8"] = {"unasked": un}
    # D9 케이스 이름(기록 + ② 확인)
    used = set()
    for row in key_rows.values():
        for col in CASE_NAME_COLS:
            t = {"candidate_name": "candidate_table", "target_name": "target_table", "name_ko": "subject_table"}[col]
            if row.get(t) == "procedure_case" and row.get(col) and row[col] in answer:
                if not (named_cases and row[col] in named_cases):
                    used.add(row[col])
    out["D9"] = {"case_names_used": sorted(used)}
    # D10 읽지 않은 것(B · C) — 답의 이름 → 코드 가운데 그 열이 읽은 행의 키 코드 칸 · 곁 칸 어디에도 없는 것
    if rg is None:
        out["D10"] = NA
    else:
        rgs = set(rg)
        seen_codes = {c for r in key.get("rows") or [] if r["gid"] in rgs for c in (*r["codes"], *r.get("side_codes", []))}
        for g in rgs:
            row = key_rows.get(g) or {}
            for t, c in (("subject_table", "subject_code"), ("target_table", "target_code"),
                         ("candidate_table", "candidate_code"), ("from_table", "from_code"), ("to_table", "to_code")):
                if row.get(t) and row.get(c):
                    seen_codes.add(f"{row[t]}:{row[c]}")
        out["D10"] = {"unread_codes": sorted(found - seen_codes)}
    out["D11"] = d11(answer, key, key_rows, gold)
    out["ends_with_question"] = answer.rstrip().endswith(("?", "？"))
    return out


TIME_KINDS = ("adverse", "recovery")


def _fmt(sp: tuple[str, str, str]) -> str:
    return f"{sp[0]}{sp[2]}" if sp[0] == sp[1] else f"{sp[0]}~{sp[1]}{sp[2]}"


def d11(answer: str, key: dict, key_rows: dict[str, dict], gold: GoldCodes):
    """D11 시간 섞기(G13 · G14 — 답 키 고치기 1 K5 · 세 열 같은 함수) — **부작용 · 회복을 물은 문항에서만**.

    답의 시간 꼴(「3~6개월」 · 「7일」 …) 가운데 물은 대상 자신의 부작용 · 회복 행(키 행 · 층 자기)에 없는 것이
    - G13: 물은 대상의 **효과**(g_outcome 발현 · 지속) 시간에 있으면 — 효과 기간을 부작용 시간으로 씀(효과를 함께 물었으면 안 본다)
    - G14: 다른 대상(키의 하위 · 윗층 행 · gold 의 다른 대상)의 부작용 · 회복 시간에 있으면 — 남의 시간을 물은 대상 것처럼 씀
    어느 쪽에도 없는 시간은 D4(숫자)가 잰다. 이름 · 층을 밝혔는지는 ② 가 본다(이 검사는 「밝힘」 을 읽지 않는다 — 기록 · 위반 수)."""
    kinds = set(key.get("expected_kinds") or [])
    if not kinds & set(TIME_KINDS):
        return NA
    said = text_spans(answer)
    own, other, targets = set(), set(), set()
    for r in key.get("rows") or []:
        row = key_rows.get(r["gid"]) or {}
        t = r["gold_table"]
        if t not in ("g_adverse", "g_recovery"):
            continue
        node = f"{row.get('target_table')}:{row.get('target_code')}"
        if (r.get("layer_role") or "자기") == "자기":
            own |= row_spans(row, t)
            targets.add(node)
        else:
            other |= row_spans(row, t)
    outcome = set()
    for t, node, sp in gold.times:
        if t == "g_outcome" and node in targets:
            outcome |= sp
        elif t != "g_outcome" and node not in targets:
            other |= sp
    rest = said - own
    g13 = sorted(rest & outcome) if "outcome" not in kinds else []
    g14 = sorted((rest - set(g13)) & other)
    return {"G13": [_fmt(x) for x in g13], "G14": [_fmt(x) for x in g14], "said": len(said),
            "violations": len(g13) + len(g14)}
