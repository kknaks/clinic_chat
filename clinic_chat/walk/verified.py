"""verified 행 모음 · 표지(SPEC-002 §3-6 FT3 · §4 K4 · §6-3 「B 에만 해당하는 것」).

읽기로 한 대상 × 종류에서 읽은 행은 **전부** 여기에 들어간다 — Noul · answer_ready · 크기로 빼는 길이 없다(G9).
행은 허브 응답 그대로(전 칸 — 기록용)이고 표지는 `marks` 에 따로 붙는다. LLM 에 무엇을 싣는지는 `material` 이 정한다.
판정용(`end_state`) · 표지용(`label`) 조회의 행은 여기에 넣지 않는다(K5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# 묶음 키의 대상 칸(§9-5 「키 코드 칸」 의 첫 칸 — FT1)
KEY_COLUMN = {
    "g_profile": ("subject_table", "subject_code"), "g_indication": ("candidate_table", "candidate_code"),
    "g_outcome": ("target_table", "target_code"), "g_adverse": ("target_table", "target_code"),
    "g_adverse_detail": ("target_table", "target_code"), "g_recovery": ("target_table", "target_code"),
    "g_contraindication": ("target_table", "target_code"), "g_pair": ("from_table", "from_code"),
    "g_package": (None, "package_id"), "g_offer": (None, "offer_id"), "g_scope": ("subject_table", "subject_code"),
}
GID_PREFIX = {"GPF": "g_profile", "GIN": "g_indication", "GOU": "g_outcome", "GAD": "g_adverse",
              "GAX": "g_adverse_detail", "GRC": "g_recovery", "GCT": "g_contraindication", "GPR": "g_pair",
              "GPK": "g_package", "GOF": "g_offer", "GSC": "g_scope", "GEN": "g_entry"}
BUCKETS = ("rows", "groups", "modality_only", "unreached", "same_day_packages", "holders")
CASE_COLS = ("candidate_table", "target_table", "subject_table", "from_table")
# N-1 — `procedure_name` 칸이 없는 표: 케이스 행의 말할 이름은 그 케이스의 `g_profile.procedure_name` 값
NO_PROCEDURE_NAME = ("g_outcome", "g_adverse", "g_adverse_detail", "g_recovery", "g_pair", "g_package")


def table_of(row: dict) -> str | None:
    t = row.get("gold_table")
    if t:
        return str(t)
    g = str(row.get("gid") or "")
    return GID_PREFIX.get(g.split("-", 1)[0])


def layer_of(row: dict) -> dict:
    return row.get("layer") or row.get("a_layer") or {}


def layer_depth(row: dict) -> int:
    """자손 층(HG2 — SPEC-001 §5-9)의 단 수 — 응답 `layer.depth`(없으면 1 · 0.5.1 허브)."""
    d = layer_of(row).get("depth")
    return d if isinstance(d, int) else 1


def holder_node(h: dict) -> str | None:
    t, c = h.get("holder_table"), h.get("holder_code")
    return f"{t}:{c}" if t and c else None


def site_match_by(row: dict) -> list[str]:
    """행의 `site_match[]`(HG3-4) → 맞은 길 이름(`column` · `concern_site`) — 항목은 `{by, csi_ids}` 또는 글자."""
    out = []
    for x in row.get("site_match") or []:
        b = x if isinstance(x, str) else (x.get("by") or x.get("match") or x.get("kind")) if isinstance(x, dict) else None
        if b and b not in out:
            out.append(str(b))
    return out


def group_key(row: dict) -> tuple:
    t = table_of(row) or ""
    tc, cc = KEY_COLUMN.get(t, (None, None))
    subj = (row.get(tc) if tc else None, row.get(cc) if cc else None)
    return (t, subj, layer_of(row).get("role"))


def case_node(row: dict) -> str | None:
    """케이스 행이면 그 케이스의 `표:코드`(말할 이름 = 시술 이름 — G10)."""
    for c in CASE_COLS:
        if row.get(c) == "procedure_case":
            code = row.get(c.replace("_table", "_code"))
            return f"procedure_case:{code}" if code else None
    return None


def marks(row: dict, read_as: dict | None = None) -> dict:
    """K4 표지(기록 꼴) — 층 · polarity · same_day 빈칸 · 충돌 · 근거 종류 · 케이스 행의 말할 이름."""
    t = table_of(row)
    m: dict[str, Any] = {"evidence_kind": row.get("evidence_kind")}
    lay = layer_of(row)
    if lay:
        m["layer"] = lay
    for k in ("hier", "attr"):
        if row.get(k) is not None:
            m[k] = row[k]
    if t in ("g_adverse", "g_contraindication"):
        m["polarity"] = row.get("polarity")
    if t == "g_pair":
        m["same_day_blank"] = (row.get("same_day") or "") == ""
        if row.get("conflict_id"):
            m["conflict"] = {"id": row.get("conflict_id"), "side": row.get("conflict_side")}
    if case_node(row) and t not in NO_PROCEDURE_NAME:
        m["speak_name"] = row.get("procedure_name") or ""
    if read_as:
        m["read_as"] = read_as
    if row.get("site_match"):
        m["site_match"] = row["site_match"]  # HG3 — `csi_ids[]` 는 기록에만(LLM 표지는 맞은 길 이름만)
    return m


def via_names(response: dict) -> dict[str, str]:
    """같은 응답의 행이 가진 holder 이름(`layer.holder.holder_name` — `depth` 1 행) — 자손 층 `via[]` 이름(호출 0 · A-3)."""
    out: dict[str, str] = {}
    for b in BUCKETS:
        for r in rows_in(response.get(b)):
            for lay in (r.get("layer"), r.get("a_layer"), r.get("b_layer")):
                h = (lay or {}).get("holder") or {}
                n = holder_node(h)
                if n and h.get("holder_name"):
                    out.setdefault(n, str(h["holder_name"]))
    return out


@dataclass
class VRow:
    gid: str
    gold_table: str
    ranges: list[str]
    kinds: list[str]
    depth: str  # breadth | deep
    bucket: str
    call: int  # 그 행을 낸 호출 번호(trace breadth[] · depth_reads[] 순번)
    row: dict
    marks: dict
    followup: bool = False  # 부위·고민의 후보별 profile(싣는 칸 넷째 줄)

    @property
    def range(self) -> str:
        return self.ranges[0] if self.ranges else ""

    def as_trace(self) -> dict:
        return {"gid": self.gid, "depth": self.depth}


@dataclass
class Verified:
    rows: dict[str, VRow] = field(default_factory=dict)

    def add_response(self, response: dict, *, ranges, kinds, depth: str, call_no: int,
                     read_as: dict | None = None, followup: bool = False, mark: str | None = None) -> list[str]:
        """응답의 행 전부(쪽 단위 목록 + 쪽 밖 목록 · groups 안쪽)를 넣는다. 반환 = 그 응답의 gid."""
        got: list[str] = []
        upper = response.get("inherited_from")  # HG1 — 행 0 고민의 응답이 윗고민 행을 물려받음(머리 · B-5)
        names: dict[str, str] | None = None
        for bucket in BUCKETS:
            for row in rows_in(response.get(bucket)):
                gid = str(row.get("gid"))
                got.append(gid)
                if gid in self.rows:
                    v = self.rows[gid]
                    if depth == "breadth" and v.depth != "breadth":
                        v.depth = "breadth"
                    v.ranges = list(dict.fromkeys((*v.ranges, *ranges)))
                    v.kinds = list(dict.fromkeys((*v.kinds, *kinds)))
                    v.followup = v.followup and followup
                    if read_as and "read_as" not in v.marks:
                        v.marks["read_as"] = read_as
                    _merge_marks(v, row, upper)  # W-4 — 조회별 표지는 합친다(읽은 차례와 무관)
                    if mark:
                        v.marks[mark] = True  # 0.6 BR6 · W-4 — 좁힌 조회에서도 온 행(안 거른 조회와 표지 합침)
                    continue
                mk = marks(row, read_as)
                if mark:
                    mk[mark] = True
                if upper:
                    mk["inherited_from"] = upper
                if layer_depth(row) >= 2:  # HG2 — 거친 층 이름은 같은 응답의 depth 1 행에서(호출 0) · 못 얻으면 코드만 기록
                    names = via_names(response) if names is None else names
                    via = [holder_node(x) for x in layer_of(row).get("via") or []]
                    mk["layer_via_names"] = {n: names[n] for n in via if n and n in names}
                    mk["layer_via_unnamed"] = [n for n in via if n and n not in names]
                self.rows[gid] = VRow(gid, table_of(row) or "", list(ranges), list(kinds), depth, bucket, call_no,
                                      row, mk, followup)
        return got

    def gids(self) -> list[str]:
        return list(self.rows)

    def by_table(self, table: str) -> list[VRow]:
        return [r for r in self.rows.values() if r.gold_table == table]

    def summary_for_jev(self) -> list[dict]:
        """Jev state 의 `verified` — 묶음 줄의 식별 칸만(§3-3 · §3-6 「Jev state」)."""
        out: dict[tuple, dict] = {}
        for r in self.rows.values():
            k = (r.gold_table, *group_key(r.row)[1], layer_of(r.row).get("role"), r.depth)
            d = out.setdefault(k, {"target": f"{k[1]}:{k[2]}" if k[2] else None,
                                   "name": name_of(r.row), "gold_table": r.gold_table,
                                   "layer": k[3], "rows": 0, "depth": r.depth})
            d["rows"] += 1
        return list(out.values())


def _sm_key(x) -> str:
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, sort_keys=True)


def _merge_marks(v: VRow, row: dict, upper) -> None:
    """같은 gid 가 다른 조회에서 또 오면 조회별 표지(`inherited_from` · `site_match`)를 합친다(코드 검수 v052 W-4).

    행 하나 · 표지는 조회별 — 자기 행 조회가 먼저 읽혀도 뒤 조회의 윗고민 물려받음 · 부위 맞음 표지가 사라지지 않는다."""
    if upper and not v.marks.get("inherited_from"):
        v.marks["inherited_from"] = upper
    new = row.get("site_match") or []
    if new:
        old = v.row.get("site_match") or []
        have = {_sm_key(x) for x in old}
        merged = list(old) + [x for x in new if _sm_key(x) not in have]
        if len(merged) != len(old):
            v.row = {**v.row, "site_match": merged}
            v.marks["site_match"] = merged


def name_of(row: dict) -> str:
    for c in ("name_ko", "candidate_name", "target_name", "from_name", "package_name", "offer_name", "subject_name"):
        if row.get(c):
            return str(row[c])
    return ""


def rows_in(v: Any) -> list[dict]:
    """bucket 안의 행 — groups 는 안쪽 rows[] · same_day_packages 는 패키지 묶음 안쪽까지(gid 가 있는 dict)."""
    out: list[dict] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if x.get("gid") and (x.get("gold_table") or table_of(x)):
                out.append(x)
                return
            for y in x.values():
                if isinstance(y, dict | list):
                    walk(y)
        elif isinstance(x, list):
            for y in x:
                walk(y)

    walk(v)
    return out


def _rows_in(v: Any, bucket: str = "") -> list[dict]:  # 옛 이름(답 키 스크립트 · 시험이 import)
    return rows_in(v)


def response_rows(resp: dict) -> list[dict]:
    out = []
    for b in BUCKETS:
        out.extend(rows_in(resp.get(b)))
    return out
