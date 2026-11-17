"""범위별 허용 표(SPEC-002 §3-2 · SPEC-001 §5-2) — 앱 고정. Jev 는 이 표를 바꾸지 못한다.

0.5.0 부터 범위는 **이름표**다(RG1 · RG2) — 읽기로 한 (대상의 표, 정보 종류)에서 결정론으로 끌어낸다(`walk.kinds`).
범위 고르기 요청은 없다. 범위 이름은 허브 `resolve.range` enum · `questions.csv` `range` 값과 같은 한국어 이름이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from clinic_chat.walk.kinds import ATTR4, ATTR_START, RANGE_NAME, TARGET4

VALUE_TABLES = ("concern", "effect", "adverse", "condition", "offer", "package")

# §3-2 「scope role」 칸 — 대상 쪽 조회는 하위 층의 자기 행도 읽는다(단계 ② · 층 표시)
ROLES_TARGET = ("자기", "물려받음", "하위 시술", "하위 케이스", "하위 제품")
ROLES_PRODUCT_OFFER = ("자기", "하위 제품")  # 제품 노드 × offer — 물려받음 없음(SPEC-001 §4-4)
ROLES_BASE = ("자기", "물려받음")  # 부위·고민의 후보별 profile(허브 기본값 — SPEC-001 §4-4)

OFFER_NAME = "offer_name"  # 상품 이름 부분 일치 입구(W-3) — 표가 아니라 `offer_name_contains` 글


def is_attr_start(tables) -> bool:
    """속성 시작인가 — 속성 입구가 하나 이상이고 그 밖은 `offer_name` 뿐(R2-F1).

    `offer_name` 은 속성 시작을 끄지 않는다 — 속성 시작 길에 **더해** 부른다(폭을 줄이는 길 0).
    B(`walk.front`) · 키(`evalset.key_calls`) · gate 가 같은 함수를 쓴다(BR3 — B 호출 = 키 호출).
    """
    ts = [t for t in tables if t != OFFER_NAME]
    return bool(ts) and all(t in ATTR4 for t in ts)


@dataclass(frozen=True)
class RangeDef:
    code: str
    name: str
    start_tables: tuple[str, ...]  # resolve `in_range` 의 시작 표(속성 4표는 늘 시작 표 — 따로 더함)
    depth: str | None  # 깊이 칸의 꼴(None = 없음 — 후보 Noul 없이 ② → ③-4)


RANGES: tuple[RangeDef, ...] = (
    RangeDef("procedure", "시술", ("procedure", "procedure_case", "product", "modality"), None),  # 0.5.0 — 하위 층은 ② 로
    RangeDef("concern", "부위·고민", ("concern",), None),
    RangeDef("adverse", "부작용", (*TARGET4, "adverse"), "adverse_value_side"),
    RangeDef("combo", "시술 조합", (*TARGET4, "concern"), "other_end"),
    RangeDef("same_day", "동시 가능", TARGET4, None),
    RangeDef("sequence", "순서·간격", TARGET4, "other_end_one_side"),
    RangeDef("avoid", "회피", (*TARGET4, "condition"), "condition_side"),
    RangeDef("offer", "상품", ("product", "procedure", "procedure_case", "offer"), "offer_items"),
)
BY_CODE = {r.code: r for r in RANGES}
BY_NAME = {r.name: r for r in RANGES}
ATTR_DEPTH = "attr_targets"

# 시술 조합 · 순서·간격 · 회피 의 pair 고정 갈래(§3-2 「허용 관계」) · 동시 가능 = 전 갈래 + 같은 날 조합
PAIR_CLASSES = {"combo": ("보완",), "order_interval": ("순서", "간격", "재시술 간격"), "avoid": ("회피",)}
SAME_DAY_PACKAGE_KINDS = ("같은 날 조합",)

FACET_TABLE = {
    "profile": "g_profile", "indication": "g_indication", "outcome": "g_outcome", "adverse": "g_adverse",
    "adverse_detail": "g_adverse_detail", "recovery": "g_recovery", "contraindication": "g_contraindication",
    "offer": "g_offer", "package": "g_package",
}
# facet 표에 `site_name` 칸이 있나(BR1 — 입구가 둘 이상일 때 부위 입구를 거는 필터)
SITE_NAME_FACETS = ("profile", "indication", "outcome", "adverse", "adverse_detail", "contraindication")


def range_name(code: str) -> str:
    return RANGE_NAME.get(code, code)


def start_tables(codes) -> set[str]:
    out: set[str] = set(ATTR4)
    for c in codes:
        if c in BY_CODE:
            out |= set(BY_CODE[c].start_tables)
    return out


__all__ = ["ATTR4", "ATTR_START", "BY_CODE", "BY_NAME", "RANGES", "TARGET4"]
