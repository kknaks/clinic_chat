"""답 재료 — 싣는 칸 · 줄 번호 · 표지(SPEC-002 §3-2a 「LLM 에 싣는 칸」 · §3-6 FT1 ~ FT7 · §4 K4).

- **행은 하나도 빼지 않는다** — verified 행마다 줄 하나(줄 번호 `n` · 한 질문 안 1 부터 · 쪽을 넘어 이어짐).
- 칸은 **그 행의 정보 종류가 정한 것만**(`kinds.llm_columns`) — 깊이 결정 · Noul · 크기와 무관(`listed_only` 줄도 같은 칸).
- `gid` · 코드 · silver 칸 · `evidence_kind` 칸 · `price_mid_raw` 는 줄에 없다 → `verified_lines[]`(기록)에만.
- 늘 싣는 표지: 층(role · holder 이름 · path) · `hier.match` · `attr.column` · 깊이(`deepen` / `listed_only`) ·
  `basis`(= `evidence_kind` 값 — 「원장 진술(검증 안 됨)」 표지 · G8) · `same_day` 빈칸 · 충돌 짝의 줄 번호 ·
  케이스 행의 말할 이름(G10 · N-1) · 「물려받은 값으로 채움」(G12) · 질문의 말을 이 개념으로 읽음(TG2 · TG3 · 입구 고르기).
- 묶음 키 = (`gold_table`, 대상 칸, `layer.role`)(FT1 · FT7) · 묶음 안 같은 값은 머리에 한 번 · 다른 값은 줄별(FT2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from clinic_chat.walk.calls import NARROWED_MARK
from clinic_chat.walk.keycodes import CLASSES, classify, key_codes, key_names, value_side
from clinic_chat.walk.kinds import llm_columns
from clinic_chat.walk.verified import (
    NO_PROCEDURE_NAME,
    Verified,
    VRow,
    case_node,
    group_key,
    holder_node,
    layer_depth,
    layer_of,
    site_match_by,
)

# HG3 표지 값 — SPEC-002 §3-1 BR1 의 두 말 그대로(새 문구 아님)
SITE_MATCH_KO = {"column": "부위 글자로 맞음", "concern_site": "그 부위의 고민으로 묶임"}
# 0.6 표지 값 — SPEC-002 O-32 ④ 의 세 말 그대로(BR6 · W-4 · VP2 · SD1)
MARK_KO = {"narrowed": NARROWED_MARK, "value_pick": "질문이 가리킨 값", "same_day_package": "같은 날 근거(패키지)"}


def upper_filled(v: VRow, inherited_kinds: set[str]) -> bool:
    """「물려받은 값으로 채움」(G12) 대상 — 층 `물려받음` 행 · 또는 윗고민으로 물려받은 응답의 행(HG1 · 머리 `inherited_from`)."""
    lay = layer_of(v.row)
    return bool((lay.get("role") == "물려받음" or v.marks.get("inherited_from")) and inherited_kinds & set(v.kinds))


def via_label(v: VRow, speak_names: dict[str, str]) -> list[str] | None:
    """거친 층(`layer.via[]`)의 사람 이름 — 같은 응답(호출 0) · 없으면 `label` 조회로 얻은 이름. 하나라도 못 얻으면 None(지어내지 않음)."""
    out = []
    got = v.marks.get("layer_via_names") or {}
    for x in layer_of(v.row).get("via") or []:
        n = holder_node(x)
        name = got.get(n) or speak_names.get(n or "")
        if not name:
            return None
        out.append(name)
    return out or None

CAND_COLS = (("candidate_table", "candidate_code"), ("holder_table", "holder_code"), ("target_table", "target_code"),
             ("to_table", "to_code"), ("from_table", "from_code"), ("subject_table", "subject_code"))


def _canon(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _decision(v: VRow, decisions: dict[str, dict] | None) -> str | None:
    if not decisions:
        return None
    for tc, cc in CAND_COLS:
        node = f"{v.row.get(tc)}:{v.row.get(cc)}"
        if node in decisions:
            d = decisions[node]
            return d.get("closed_as") or d.get("decision")
    if v.row.get("procedure_code"):
        d = decisions.get(f"procedure:{v.row.get('procedure_code')}")
        return (d.get("closed_as") or d.get("decision")) if d else None
    return None


def llm_marks(v: VRow, *, decisions: dict | None, speak_names: dict[str, str], inherited_kinds: set[str]) -> dict:
    """LLM 에 가는 표지 — 코드 · gid 없이 사람이 읽는 값만."""
    m: dict[str, Any] = {}
    lay = layer_of(v.row)
    if lay:
        m["layer"] = lay.get("role")
        holder = (lay.get("holder") or {}).get("holder_name")
        if holder:
            m["holder"] = holder
        if lay.get("path"):
            m["path"] = lay.get("path")  # N-4 — g_scope 의 path 는 층 표지의 일부로(link 는 기록에만)
    if (v.row.get("hier") or {}).get("match"):
        m["hier"] = v.row["hier"]["match"]
    if (v.row.get("attr") or {}).get("column"):
        m["attr"] = v.row["attr"]["column"]
    dec = _decision(v, decisions)
    if dec:
        m["depth"] = dec
    if v.row.get("evidence_kind") is not None:
        m["basis"] = v.row.get("evidence_kind")
    if v.gold_table == "g_pair" and (v.row.get("same_day") or "") == "":
        m["same_day_blank"] = True
    cn = case_node(v.row)
    if cn:
        if v.gold_table in NO_PROCEDURE_NAME:
            if cn in speak_names:
                m["speak_name"] = speak_names[cn]
        else:
            m["speak_name"] = v.row.get("procedure_name") or ""
    if upper_filled(v, inherited_kinds):
        m["inherited_fill"] = True
    if layer_depth(v.row) >= 2:  # HG2 — 몇 단 아래 층인가(늘) · 거친 층 이름(얻었을 때만 — A-3)
        m["layer_depth"] = layer_depth(v.row)
        via = via_label(v, speak_names)
        if via:
            m["layer_via"] = via
    sm = site_match_by(v.row)
    if sm:
        m["site_match"] = [SITE_MATCH_KO.get(b, b) for b in sm]
    ra = v.marks.get("read_as")
    if ra:
        m["read_as"] = ra.get("by")
    for k, ko in MARK_KO.items():  # 0.6 — 좁힌 것(BR6 · W-4) · 질문이 가리킨 값(VP2) · 같은 날 근거(SD1)
        if v.marks.get(k):
            m[k] = ko
    if v.marks.get("same_day_layer"):
        m["same_day_layer"] = v.marks["same_day_layer"]  # SD1 — 항목이 그 대상의 윗층 · 하위(scope role)
    tk = time_kind(v)
    if tk:
        m["time_of"] = tk  # K5 · G13 — 효과(발현 · 지속) 시간을 부작용 시간으로 쓰지 않게
        if tk != TIME_OF["g_outcome"] and lay.get("role") not in (None, "", "자기"):
            m["about"] = v.row.get("target_name") or ""  # G14 — 다른 대상(하위 · 윗층)의 부작용 · 회복 시간
    return m


TIME_COLS = ("onset_text", "onset_min", "onset_max", "duration_text", "duration_min", "duration_max",
             "text", "min", "max")
TIME_OF = {"g_outcome": "효과(발현 · 지속) 시간 — 부작용 시간 아님", "g_adverse": "부작용 시간",
           "g_recovery": "회복 기간"}


def time_kind(v: VRow) -> str | None:
    """시간 칸에 값이 있는 효과 · 부작용 · 회복 행 → 그 시간이 무엇의 시간인가(K5 · G13 · G14 표지)."""
    if v.gold_table not in TIME_OF:
        return None
    cols = TIME_COLS if v.gold_table == "g_recovery" else TIME_COLS[:6]
    return TIME_OF[v.gold_table] if any(str(v.row.get(c) or "") for c in cols) else None


@dataclass
class Material:
    groups: list[dict] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)  # verified_lines[]
    absent_columns: dict[str, list[str]] = field(default_factory=dict)  # 허브 행에 없는 싣는 칸(표 → 칸) — 기록
    expected_columns: dict[int, tuple[str, ...]] = field(default_factory=dict)  # n → 그 줄의 싣는 칸
    roster: dict = field(default_factory=dict)  # FT8 이름 목록(기록 꼴 — {entry, value, layer: [{code, name}]})

    def n_of(self) -> dict[str, int]:
        return {x["gid"]: x["n"] for x in self.lines}


def build(verified: Verified, *, decisions: dict | None = None, speak_names: dict[str, str] | None = None,
          inherited_kinds: set[str] | None = None) -> Material:
    out = Material()
    speak_names = speak_names or {}
    inherited_kinds = inherited_kinds or set()
    recs: list[tuple[tuple, dict, dict, int]] = []
    conflicts: dict[str, list[int]] = {}
    for n, v in enumerate(verified.rows.values(), 1):
        cols = llm_columns(v.gold_table, v.kinds, followup=v.followup)
        out.expected_columns[n] = cols
        body = {}
        for c in cols:
            if c in v.row:
                body[c] = v.row[c]
            else:
                body[c] = ""
                absent = out.absent_columns.setdefault(v.gold_table, [])
                if c not in absent:
                    absent.append(c)
        marks = llm_marks(v, decisions=decisions, speak_names=speak_names, inherited_kinds=inherited_kinds)
        if v.row.get("conflict_id"):
            conflicts.setdefault(str(v.row["conflict_id"]), []).append(n)
        k = group_key(v.row)
        recs.append(((k[0], _canon(k[1]), _canon(k[2])), body, marks, n))
        out.lines.append({"n": n, "gid": v.gid, "gold_table": v.gold_table, "silver_table": v.row.get("silver_table"),
                          "silver_id": v.row.get("silver_id"), "evidence_kind": v.row.get("evidence_kind"),
                          "kind": list(v.kinds), "range": list(v.ranges), "layer": layer_of(v.row) or None,
                          "depth": v.depth,
                          **({"site_match": v.row["site_match"]} if v.row.get("site_match") else {}),  # HG3 csi_ids
                          **({"inherited_from": v.marks["inherited_from"]} if v.marks.get("inherited_from") else {}),
                          **({"layer_via_unnamed": v.marks["layer_via_unnamed"]}
                             if v.marks.get("layer_via_unnamed") and not marks.get("layer_via") else {}),
                          **({"marks06": [k for k in (*MARK_KO, "same_day_layer") if v.marks.get(k)]}  # 0.6 표지(기록 · 보고서)
                             if any(v.marks.get(k) for k in (*MARK_KO, "same_day_layer")) else {})})
    by_n = {r[3]: r for r in recs}
    for ns in conflicts.values():
        for n in ns:
            by_n[n][2]["conflict_lines"] = [x for x in ns if x != n]
            side = verified.rows[out.lines[n - 1]["gid"]].row.get("conflict_side")
            if side:
                by_n[n][2]["conflict_side"] = side
    order: list[tuple] = []
    groups: dict[tuple, list[tuple]] = {}
    for r in recs:
        if r[0] not in groups:
            groups[r[0]] = []
            order.append(r[0])
        groups[r[0]].append(r)
    for i, k in enumerate(order, 1):
        rows = groups[k]
        same = _same(rows, 1)
        same_marks = _same(rows, 2)
        body = []
        for _, b, m, n in rows:
            d: dict[str, Any] = {"n": n}
            d.update({c: x for c, x in b.items() if c not in same})
            mm = {c: x for c, x in m.items() if c not in same_marks}
            if mm:
                d["marks"] = mm
            body.append(d)
        out.groups.append({"group": i, "table": k[0], "same": same, "marks": same_marks, "lines": [r[3] for r in rows],
                           "rows": body})
    return out


def _same(rows: list[tuple], idx: int) -> dict:
    cols: list[str] = []
    for r in rows:
        for c in r[idx]:
            if c not in cols:
                cols.append(c)
    out = {}
    for c in cols:
        vals = {_canon(r[idx][c]) if c in r[idx] else "\x00missing" for r in rows}
        if len(vals) == 1 and "\x00missing" not in vals:
            out[c] = rows[0][idx][c]
    return out


def unfold(groups: list[dict]) -> list[dict]:
    """묶음 → 줄마다 {n, 칸…, marks}(머리 값을 되붙임) — K1 의 「행을 전부 · 칸을 전부 실었나」 대조용."""
    out = []
    for g in groups:
        for r in g["rows"]:
            rec = dict(g["same"])
            rec.update({c: x for c, x in r.items() if c != "marks"})
            marks = dict(g["marks"])
            marks.update(r.get("marks") or {})
            rec["marks"] = marks
            out.append(rec)
    return out


def to_lines(groups: list[dict]) -> str:
    lines = []
    for g in groups:
        head = {"group": g["group"], "same": g["same"]}  # `table`(gold 표 이름)은 기록에만(§3-2a — 코드 검수 v05 N-5)
        if g["marks"]:
            head["marks"] = g["marks"]
        head["lines"] = g["lines"]
        lines.append(json.dumps(head, ensure_ascii=False))
        for r in g["rows"]:
            lines.append(json.dumps(r, ensure_ascii=False))
    return "\n".join(lines)


# ───── 0.6 이름 목록(FT8 · CP2 ~ CP4) — 키 코드 칸 · 갈래는 답 키 · D3 와 같은 함수(`walk.keycodes`) ─────

def roster(reads, verified: Verified, entry_nodes) -> dict:
    """`verified` 행의 키 코드 → {entry, value, layer: [{code, name}]}(verified 순서 · 결정론). 갈래는 그 행을 낸 조회의 쪽으로
    (`keycodes.value_side`) — 같은 gid 가 여러 조회에서 오면 갈래는 입구 > 값 > 층."""
    occ: list[tuple[dict, bool]] = []
    for r in reads:
        if r.call.purpose not in ("read", "depth"):
            continue
        vs = value_side(r.call.request, r.call.meta)
        for g in r.gids:
            if g in verified.rows:
                occ.append((verified.rows[g].row, vs))
    cls = classify(occ, entry_nodes)
    names: dict[str, str] = {}
    order: list[str] = []
    for v in verified.rows.values():
        for c, n in key_names(v.row):
            if c not in names or (n and not names[c]):
                names[c] = n
        for c in key_codes(v.row):
            if c not in order:
                order.append(c)
    out: dict[str, list[dict]] = {k: [] for k in CLASSES}
    for c in order:
        out[cls.get(c, "value")].append({"code": c, "name": names.get(c, "")})
    return out if any(out.values()) else {}


def roster_text(r: dict) -> str:
    """LLM 에 싣는 꼴 — 이름뿐(코드 없음) · 값 쪽 / 층 쪽을 갈라 표(입구 표 이름)별로 묶음 · 이름 하나는 한 번만(값 쪽 먼저)."""
    if not r:
        return ""
    seen: set[str] = set()
    body: dict[str, dict[str, list[str]]] = {}
    for cls in ("value", "layer"):
        grp: dict[str, list[str]] = {}
        for x in r.get(cls) or []:
            n = x["name"]
            if not n or n in seen:
                continue
            seen.add(n)
            grp.setdefault(x["code"].split(":", 1)[0], []).append(n)
        if grp:
            body[cls] = grp
    return json.dumps(body, ensure_ascii=False) if body else ""


def _norm(s: str) -> str:
    return "".join(str(s or "").split()).lower()


def answer_check(answer: str, r: dict) -> dict:
    """CP4 — 이름 목록의 이름이 답 글에 나왔나(이름 글자 대조 — 채점 전용 env 없이 재료의 이름 목록 자신으로). 기록만."""
    a = _norm(answer)
    out: dict = {"roster": {k: r.get(k) or [] for k in CLASSES}, "said": {}, "missing": {}, "missing_codes": {}}
    seen: set[str] = set()
    for cls in ("value", "layer"):
        said, miss, mcodes = 0, [], []
        for x in r.get(cls) or []:
            n = x["name"]
            if not n or n in seen:
                continue
            seen.add(n)
            if _norm(n) in a:
                said += 1
            else:
                miss.append(n)
                mcodes.append(x["code"])
        out["said"][cls], out["missing"][cls], out["missing_codes"][cls] = said, miss, mcodes
    return out
