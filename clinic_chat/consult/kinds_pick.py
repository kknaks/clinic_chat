"""정보 종류(SPEC-008 §3-1 KS1 ~ KS8 · WORK-017 WP-6) — 이해 호출의 `kinds[]` = yes · 안 고른 것 = no.

걷기의 `_decide_kinds`(`walk/front.py`)를 고치지 않고 같은 뜻을 상담 쪽에 둔다. 갈리는 곳은 넷뿐이다:
KS2(`top` 없음 — `kinds` 가 `[]` 면 KS6 기본 묶음) · KS4(`kd10` 없음 — 사이 값이 없다) · KS7(KD11 = 그 범위에 `yes` · `entry`
· `kd9` 종류가 있으면 안 고른 기본 묶음 종류는 `offered`) · KS8(`required` = `yes` · `entry` · `offered` = KS7 로 안 읽은 것뿐).
KS3(KD5 `entry`) · KS5(KD9) · KS6(KD6) · KD4 · KD7 은 그대로다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clinic_chat.walk.front import KD9_SET, kind_rows, ranges_for
from clinic_chat.walk.kinds import DEFAULTS, ENTRY_KIND, HOME_RANGE, KIND_CODES, KIND_NAME, kinds_for_table, range_of
from clinic_chat.walk.ranges import is_attr_start

REQUIRED_BY = ("yes", "entry")  # KS8
STRONG_BY = ("yes", "entry", "kd9")  # KS7


@dataclass
class Kinds:
    kinds: list[str] = field(default_factory=list)
    required: list[str] = field(default_factory=list)
    offered: list[dict] = field(default_factory=list)
    by: dict[str, str] = field(default_factory=dict)
    ranges: list[str] = field(default_factory=list)
    attr_start: bool = False
    applicable: dict[str, bool] = field(default_factory=dict)
    kinds_by: str = "understand"


def decide(entries, kinds: list[str], coverage: dict) -> Kinds:
    attr = is_attr_start(e.table for e in entries)
    by: dict[str, str] = {k: "yes" for k in KIND_CODES if k in kinds}  # KS1 · KS2(top 없음)
    for e in entries:  # KS3 — 입구가 이름 댄 정보
        k = ENTRY_KIND.get(e.table)
        if k and k not in by and range_of(k, e.table, attr):
            by[k] = "entry"
    if by.get("overview") == "yes":  # KS5
        for k in KD9_SET:
            by.setdefault(k, "kd9")
    applicable = {k: any(range_of(k, e.table, attr) for e in entries) for k in KIND_CODES}
    kinds_by = "understand"
    if not any(applicable[k] for k in by):  # KS6
        kinds_by = "default"
        for e in entries:
            home = HOME_RANGE.get(e.table)
            ks = [k for k in DEFAULTS.get(home or "", ()) if range_of(k, e.table, attr)] or list(kinds_for_table(e.table, attr))
            for k in ks:
                by.setdefault(k, "default")
    ranges = ranges_for((e.table, k, attr) for e in entries for k in KIND_CODES if k in by)  # KS7 범위 식
    strong = set(ranges_for((e.table, k, attr) for e in entries for k in KIND_CODES if by.get(k) in STRONG_BY))
    kd11: list[str] = []
    for r in list(ranges):
        for k in DEFAULTS.get(r, ()):
            if k in by:
                continue
            if kinds_by != "default" and r in strong:
                if k not in kd11:
                    kd11.append(k)
                continue
            by[k] = "default"
    out = [k for k in KIND_CODES if k in by and applicable[k]]
    nodes = [e.node for e in entries]
    offered = [{"code": k, "name": KIND_NAME[k], "rows": kind_rows(coverage, nodes, k), "by": "kd11"}
               for k in KIND_CODES if k not in by and applicable[k] and k in kd11]
    return Kinds(out, [k for k in out if by[k] in REQUIRED_BY], offered, by, ranges, attr, applicable, kinds_by)
