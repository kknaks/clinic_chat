"""정보 종류 표(SPEC-002 §3-2a · SPEC-001 §5-8 — 같은 14 종류) — 앱 고정. Jev 는 이 표를 바꾸지 못한다.

- 종류 14 의 **순서** = 표의 행 순서(`kinds_catalog` · `kind_{k}` 의 k).
- 대상의 표 → 범위(이름표 · RG1) · 범위의 기본 묶음(KD3) · 읽는 조회(→ `calls.plan_calls`) · LLM 에 싣는 칸(→ `material`).
- 싣는 칸 이름은 gold CSV 헤더 그대로다. 표에 없는 칸은 싣지 않는다(시험이 헤더와 대조한다).
"""

from __future__ import annotations

from dataclasses import dataclass

TARGET4 = ("modality", "procedure", "procedure_case", "product")
ATTR4 = ("material", "method", "anatomy", "site")


@dataclass(frozen=True)
class Kind:
    code: str
    name: str  # Jev 에 주는 영어 이름(§3-2a 그대로 — KD8 이 쓴 문구)


KINDS: tuple[Kind, ...] = (
    Kind("overview", "what it is (overview, category, device/material/method)"),
    Kind("mechanism", "how it works (mechanism / principle)"),
    Kind("indication", "what it is used for / what is done for a concern"),
    Kind("outcome", "effects and how long they take / last"),
    Kind("lineup", "what belongs under it (products, procedures, cases)"),
    Kind("adverse", "side effects / adverse events"),
    Kind("recovery", "recovery / downtime"),
    Kind("contraindication", "who should not have it (patient conditions)"),
    Kind("avoid", "what must not be combined with it"),
    Kind("combo", "what goes well together with it"),
    Kind("package", "packages / programs that include it or target a concern"),
    Kind("same_day", "whether things can be done on the same day"),
    Kind("order_interval", "order, interval, repeat interval"),
    Kind("offer", "products for sale and prices"),
)
KIND_CODES: tuple[str, ...] = tuple(k.code for k in KINDS)
KIND_NAME = {k.code: k.name for k in KINDS}
SAFETY_KINDS = ("adverse", "recovery", "contraindication", "avoid")  # KD7 — 묻지 않으면 기본 묶음에 없다

# 범위 코드(이름표 — RG2). 한국어 이름은 허브 `resolve.range` enum · `questions.csv` 의 `range` 값.
ATTR_START = "attr_start"
RANGE_NAME = {"procedure": "시술", "concern": "부위·고민", "adverse": "부작용", "combo": "시술 조합",
              "same_day": "동시 가능", "sequence": "순서·간격", "avoid": "회피", "offer": "상품",
              ATTR_START: "속성 시작"}

# (종류 → {표 묶음: 범위}) — 「대상의 표 → 범위」 칸. 표 묶음 = "T"(대상 4표) · "A"(속성 4표 — 속성 시작일 때만) · 그 밖은 표 이름.
RANGE_OF: dict[str, dict[str, str]] = {
    "overview": {"T": "procedure", "A": ATTR_START},
    "mechanism": {"T": "procedure"},
    "indication": {"T": "procedure", "concern": "concern", "A": ATTR_START},
    "outcome": {"T": "procedure", "effect": "procedure", "A": ATTR_START},
    "lineup": {"T": "procedure"},
    "adverse": {"T": "adverse", "adverse": "adverse", "A": ATTR_START},
    "recovery": {"T": "adverse"},
    "contraindication": {"T": "avoid", "condition": "avoid", "A": ATTR_START},
    "avoid": {"T": "avoid"},
    "combo": {"T": "combo"},
    "package": {"T": "combo", "concern": "combo", "package": "combo"},
    "same_day": {"T": "same_day"},
    "order_interval": {"T": "sequence"},
    "offer": {"T": "offer", "offer": "offer", "A": ATTR_START},
}

# 범위의 기본 묶음(「기본」 칸 ✓) — KD3. 묻지 않은 안전 종류는 시술 · 부위·고민 · 속성 시작의 기본에 없다(KD7).
DEFAULTS: dict[str, tuple[str, ...]] = {
    "procedure": ("overview",), "concern": ("indication",), ATTR_START: ("overview", "indication"),
    "adverse": ("adverse",), "avoid": ("contraindication", "avoid"), "combo": ("combo", "package"),
    "same_day": ("same_day",), "sequence": ("order_interval",), "offer": ("offer",),
}

# 속성 표가 낼 수 있는 맞춤 facet(SPEC-001 §4-4 「속성 쪽」 표 · 재료 2 · 방법 1 · 해부 2 · 부위 7)
ATTR_FACETS = {
    "material": ("profile", "offer"),
    "method": ("profile",),
    "anatomy": ("profile", "adverse_detail"),
    "site": ("profile", "indication", "outcome", "adverse", "adverse_detail", "contraindication", "offer"),
}
# 종류 → 속성 쪽에서 읽는 facet
ATTR_KIND_FACETS = {"overview": ("profile",), "indication": ("indication",), "outcome": ("outcome",),
                    "adverse": ("adverse", "adverse_detail"), "contraindication": ("contraindication",),
                    "offer": ("offer",)}

# KD6 — 대상은 있는데 종류가 전부 no 이하일 때 그 표의 「집」 범위(기본 묶음을 읽는다)
HOME_RANGE = {**{t: "procedure" for t in TARGET4}, **{t: ATTR_START for t in ATTR4}, "concern": "concern",
              "adverse": "adverse", "condition": "avoid", "offer": "offer", "package": "combo", "effect": "procedure"}

# KD5 — 대상 개념의 표가 이름 댄 정보 종류
ENTRY_KIND = {"condition": "contraindication", "adverse": "adverse", "offer": "offer", "package": "package",
              "concern": "indication", "effect": "outcome"}

# 종류가 행을 내는 표(K5 — 「행의 표 ∈ 읽기로 한 종류가 허용한 표」)
KIND_TABLES: dict[str, tuple[str, ...]] = {
    "overview": ("g_profile",), "mechanism": ("g_profile",), "indication": ("g_indication", "g_profile"),
    "outcome": ("g_outcome",), "lineup": ("g_scope",), "adverse": ("g_adverse", "g_adverse_detail"),
    "recovery": ("g_recovery",), "contraindication": ("g_contraindication",), "avoid": ("g_pair",),
    "combo": ("g_pair",), "package": ("g_package",), "same_day": ("g_pair", "g_package"),
    "order_interval": ("g_pair",), "offer": ("g_offer",),
}


def group_of(table: str, attr_start: bool) -> str | None:
    if table in TARGET4:
        return "T"
    if table in ATTR4:
        return "A" if attr_start else None
    return table


def range_of(kind: str, table: str, attr_start: bool) -> str | None:
    """(대상의 표, 종류) → 범위. 그 표에 그 종류의 조회가 없으면 None(「—」 — KD4 건너뜀)."""
    g = group_of(table, attr_start)
    if g is None:
        return None
    if g == "A":
        facets = ATTR_FACETS.get(table, ())
        if not any(f in facets for f in ATTR_KIND_FACETS.get(kind, ())):
            return None
    return RANGE_OF.get(kind, {}).get(g)


def kinds_for_table(table: str, attr_start: bool) -> tuple[str, ...]:
    return tuple(k for k in KIND_CODES if range_of(k, table, attr_start))


# ───── LLM 에 싣는 칸(§3-2a 표 13 줄 — 이름은 gold CSV 헤더 그대로 · O-28 제안) ─────

OVERVIEW_COLUMNS = ("materials", "methods", "tissue_name", "anatomy_name", "site_name", "product_kind",
                    "organizations", "n_cases", "n_products", "overview", "description")  # SPEC-001 §5-8 개요 칸
MECHANISM_COLUMNS = ("mechanism_code", "mechanism_name", "mechanism_text")  # SPEC-001 §5-8 원리 칸

LLM_COLUMNS: dict[tuple[str, str], tuple[str, ...]] = {
    ("g_profile", "overview"): ("name_ko", "name_en", "modality_name", "procedure_name", "materials", "methods",
                                "tissue_name", "anatomy_name", "site_name", "product_kind", "organizations",
                                "n_cases", "n_products", "overview", "description"),
    ("g_profile", "mechanism"): ("name_ko", "modality_name", "procedure_name", "mechanism_name", "mechanism_text"),
    ("g_profile", "indication"): ("name_ko", "modality_name", "procedure_name", "mechanism_name"),
    ("g_indication", "indication"): ("asked_concern_name", "concern_name", "concern_match", "site_name",
                                     "target_name", "reach", "candidate_name", "procedure_name", "case_site_name",
                                     "conditions", "note"),
    ("g_outcome", "outcome"): ("target_name", "effect_name", "site_name", "onset_text", "onset_min", "onset_max",
                               "onset_unit", "onset_qualifier", "peak_text", "peak_min", "peak_max", "peak_unit",
                               "peak_qualifier", "duration_text", "duration_min", "duration_max", "duration_unit",
                               "duration_qualifier", "note"),
    ("g_scope", "lineup"): ("subject_name", "holder_name", "role"),
    ("g_adverse", "adverse"): ("target_name", "adverse_name", "adverse_top_name", "site_name", "polarity",
                               "severity_text", "onset_text", "onset_min", "onset_max", "onset_unit",
                               "onset_qualifier", "duration_text", "duration_min", "duration_max", "duration_unit",
                               "duration_qualifier", "freq_value_text", "freq_min", "freq_max", "freq_unit",
                               "freq_qualifier", "freq_denominator", "freq_grade_text", "ratings", "n_factor",
                               "n_guidance", "note"),
    ("g_adverse_detail", "adverse"): ("detail_kind", "adverse_name", "target_name", "kind", "timing_text",
                                      "level_text", "text", "condition_name", "site_name", "anatomy_name"),
    ("g_recovery", "recovery"): ("target_name", "text", "min", "max", "unit", "qualifier"),
    ("g_contraindication", "contraindication"): ("target_name", "condition_name", "site_name", "polarity",
                                                 "level_text", "period_text", "period_min", "period_max",
                                                 "period_unit", "period_qualifier", "period_from", "reason_text"),
    ("g_pair", "pair"): ("from_name", "to_name", "kind", "question_class", "self_pair", "level_text",
                         "interval_text", "interval_min", "interval_max", "interval_unit", "interval_qualifier",
                         "order", "same_day", "same_day_text", "text", "evidence_text", "goal_effects",
                         "conflict_side"),
    ("g_package", "package"): ("package_name", "package_kind", "concern_name", "conditions", "step", "item_name",
                               "session_text", "text", "expected_text", "duration_text", "evidence_text",
                               "n_schedule"),
    ("g_offer", "offer"): ("offer_name", "modality_name", "price_low", "price_high", "currency", "quantities",
                           "item_name", "item_text", "item_note", "reach", "procedure_name", "sites", "concerns",
                           "effects", "materials", "variant_text", "condition_text", "product_origin"),
}
PAIR_KINDS = ("avoid", "combo", "same_day", "order_interval")
# 칸 이름으로는 기록에만 두는 것(§3-2a 「기록에만 두는 칸」) — 싣는 칸 표에 없어야 한다(시험이 본다)
RECORD_ONLY = ("gid", "gold_table", "silver_table", "silver_id", "freq_silver_id", "evidence_kind", "offer_id",
               "package_id", "adverse_risk_id", "conflict_id", "name_en_source", "price_mid_raw")


def llm_columns(table: str, kinds: tuple[str, ...] | list[str] | set[str], *, followup: bool = False) -> tuple[str, ...]:
    """한 행의 싣는 칸 — 그 행의 종류들의 칸을 합친다(같은 profile 행을 개요 · 원리가 함께 읽으면 합침).

    `followup` = 부위·고민의 후보별 profile(넷째 줄 — 이름 · 계열 · 시술 · 원리 이름).
    """
    if table == "g_profile" and followup:
        return LLM_COLUMNS[("g_profile", "indication")]
    out: list[str] = []
    for k in KIND_CODES:
        if k not in kinds:
            continue
        key = (table, "pair" if table == "g_pair" else ("package" if table == "g_package" else
                                                       ("adverse" if table == "g_adverse_detail" else k)))
        for c in LLM_COLUMNS.get(key, ()):
            if c not in out:
                out.append(c)
    if not out and table == "g_profile":  # 깊이에서 읽은 profile(상대 끝 · 상품의 시술) — 개요 칸
        out = list(LLM_COLUMNS[("g_profile", "overview")])
    return tuple(out)


def has_kind_value(row: dict, kind: str) -> bool:
    """RQ1 · 답 키(N-2) — `overview` · `mechanism` 은 그 종류의 칸에 값이 하나라도 있는 profile 행만 그 종류의 행이다."""
    if kind == "overview":
        return any(_filled(row.get(c)) for c in OVERVIEW_COLUMNS)
    if kind == "mechanism":
        return any(_filled(row.get(c)) for c in MECHANISM_COLUMNS)
    return True


def _filled(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, list | dict):
        return bool(v)
    return True
