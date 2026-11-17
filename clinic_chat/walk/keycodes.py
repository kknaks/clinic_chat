"""키 코드 칸 · 갈래(SPEC-002 §9-5 「키 코드 칸」 · §3-7-6 DU2 · §3-6 FT8) — **답 키 · D3 · B 의 이름 목록 · 값 고르기가 같은 함수.**

답을 만드는 열(B)도 import 한다 — 채점 전용 env · 로컬 gold 를 읽지 않는다(행에서만 뽑는다).

갈래(DU2 · 행마다 결정론 — 한 코드가 둘에 걸리면 입구 > 값 > 층):
- **입구** = 기대 대상(키) · B 의 대상(이름 목록) 자신
- **층** = 대상 쪽 조회(시작 입구가 대상 4표)의 `g_pair` · `g_package` · `g_offer` 밖 행에서 대상 4표 코드(그 행을 가진 대상)
- **값** = 그 밖 — 대상 쪽 조회의 값 칸(고민 · 효과 · 사건 · 조건) · 값 쪽 조회(고민 · 사건 · 조건 · 속성 · 상품 이름 시작)의 행 ·
  `g_pair` 의 끝 · 패키지 · 상품
"""

from __future__ import annotations

from collections.abc import Iterable

from clinic_chat.walk.kinds import TARGET4
from clinic_chat.walk.verified import table_of

# §9-5 「키 코드 칸」 — (표 칸 | 고정 표, 코드 칸) · 곁 칸
KEY_CODE = {
    "g_profile": ([("subject_table", "subject_code")],
                  [("modality", "modality_code"), ("procedure", "procedure_code"), ("mechanism", "mechanism_code")]),
    "g_indication": ([("candidate_table", "candidate_code"), ("procedure", "procedure_code"), ("concern", "concern_code")],
                     [("target_table", "target_code"), ("concern", "asked_concern_code"), ("site", "site_code")]),
    "g_outcome": ([("target_table", "target_code"), ("effect", "effect_code")], []),
    "g_adverse": ([("target_table", "target_code"), ("adverse", "adverse_code")], []),
    "g_adverse_detail": ([("target_table", "target_code")], []),
    "g_recovery": ([("target_table", "target_code")], []),
    "g_contraindication": ([("target_table", "target_code"), ("condition", "condition_code")], []),
    "g_pair": ([("from_table", "from_code"), ("to_table", "to_code")], []),
    "g_package": ([("package", "package_id"), ("item_table", "item_code"), ("concern", "concern_code")],
                  [("procedure", "item_procedure_code")]),
    "g_offer": ([("offer", "offer_id"), ("item_table", "item_code"), ("procedure", "procedure_code")],
                [("modality", "modality_code")]),
}
# 키 코드 칸의 이름 칸(그 행의 이름 — 이름 목록 · 값 고르기)
NAME_COLS = {
    "subject_code": ("subject_name", "name_ko"), "candidate_code": ("candidate_name",), "target_code": ("target_name",),
    "procedure_code": ("procedure_name",), "concern_code": ("concern_name",), "effect_code": ("effect_name",),
    "adverse_code": ("adverse_name",), "condition_code": ("condition_name",), "from_code": ("from_name",),
    "to_code": ("to_name",), "package_id": ("package_name",), "item_code": ("item_name",), "offer_id": ("offer_name",),
}
VALUE_ROW_TABLES = ("g_pair", "g_package", "g_offer")  # 이 표의 행은 대상 쪽 조회에서도 값(상대 끝 · 패키지 · 상품)
# 값 고르기(VP1) — 값 종류 → (값 표, 행 표, 값 코드 칸)
VALUE_KIND = {"adverse": ("adverse", "g_adverse", "adverse_code"),
              "contraindication": ("condition", "g_contraindication", "condition_code"),
              "outcome": ("effect", "g_outcome", "effect_code"),
              "indication": ("concern", "g_indication", "concern_code")}
CLASSES = ("entry", "value", "layer")
_RANK = {"entry": 0, "value": 1, "layer": 2}


def _codes(row: dict, cols: list[tuple[str, str]]) -> list[str]:
    out = []
    for a, b in cols:
        table = row.get(a) if a.endswith("_table") else a
        code = row.get(b)
        if table and code:
            out.append(f"{table}:{code}")
    return out


def key_codes(row: dict) -> list[str]:
    """한 행의 키 코드(§9-5 첫 칸 묶음)."""
    t = table_of(row) or ""
    return _codes(row, KEY_CODE[t][0]) if t in KEY_CODE else []


def key_names(row: dict) -> list[tuple[str, str]]:
    """한 행의 (키 코드, 그 행의 이름 칸 값 — 없으면 "")."""
    t = table_of(row) or ""
    out = []
    for a, b in KEY_CODE.get(t, ([], []))[0]:
        c = _codes(row, [(a, b)])
        if c:
            name = next((str(row[n]) for n in NAME_COLS.get(b, ()) if row.get(n)), "")
            out.append((c[0], name))
    return out


def value_side(request: dict, meta: dict | None = None) -> bool:
    """조회의 쪽 — 시작 입구가 대상 4표가 아니면 값 쪽(고민 · 사건 · 조건 · 효과 · 속성 · 상품 · 상품 이름). 고민 후보의 이어
    읽기(후보별 profile — `meta.from_group`)는 그 고민 쪽 조회의 것이다."""
    if (meta or {}).get("from_group") or request.get("offer_name_contains") or request.get("concern_code"):
        return True
    node = str(request.get("node") or request.get("a") or "")
    return node.split(":", 1)[0] not in TARGET4


def code_class(code: str, table: str, is_value_side: bool, entry_nodes) -> str:
    if code in entry_nodes:
        return "entry"
    if is_value_side or table in VALUE_ROW_TABLES or code.split(":", 1)[0] not in TARGET4:
        return "value"
    return "layer"


def classify(occurrences: Iterable[tuple[dict, bool]], entry_nodes) -> dict[str, str]:
    """(행, 값 쪽 조회인가) 들 → {키 코드: 갈래}. 한 코드가 둘에 걸리면 입구 > 값 > 층."""
    ents = set(entry_nodes)
    out: dict[str, str] = {}
    for row, vs in occurrences:
        t = table_of(row) or ""
        for c in key_codes(row):
            k = code_class(c, t, vs, ents)
            if c not in out or _RANK[k] < _RANK[out[c]]:
                out[c] = k
    return out


def by_class(codes: list[str], classes: dict[str, str]) -> dict[str, list[str]]:
    """키 `codes_by_class{entry, value, layer}` — `codes[]` 순서 · 합집합 = `codes[]` · 겹침 0(갈래가 없는 코드는 값)."""
    out: dict[str, list[str]] = {k: [] for k in CLASSES}
    for c in codes:
        out[classes.get(c, "value")].append(c)
    return out
