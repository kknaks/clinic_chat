"""걸음 1 이웃 순서 표(SPEC-006 v2.4 §3-4-6) — `walk_order.json` = 프로필 레포 `spec006-v24-recount.json` 의 `order_v24` 복사본."""

from __future__ import annotations

import json
import os
from pathlib import Path

from clinic_chat.cases import case3
from clinic_chat.config import Settings

SRC = Path(os.environ.get("SPEC006_RECOUNT_JSON", ""))  # 프로필 레포 원본(레포 밖 · 있으면 같은지 대조)
T = json.loads(case3.ORDER_FILE.read_text(encoding="utf-8"))


def test_same_as_source_when_present():
    if not os.environ.get("SPEC006_RECOUNT_JSON") or not SRC.is_file():  # 레포 밖(공개 레포 · 다른 기계) — 아래 규칙 재현만
        return
    assert T == json.loads(SRC.read_text(encoding="utf-8"))["order_v24"]


def test_rules():
    tb = T["table"]
    assert T["first_section_max_2"] == ["I-02", "I-03", "I-04", "I-05"]
    assert T["unit_link"] == {"family": "key_family", "product": "product_unit"}
    for it, row in tb.items():
        if it == "_all":
            continue
        n1 = T["place_by_intent"][it]["1홉·1쪽"]
        assert row["source"] == ("intent" if n1 >= 10 else "_all")  # 의도 1홉·1쪽 행 < 10 → 전체
        if row["source"] == "_all":
            assert row["kinds"] == tb["_all"]["kinds"]
        assert len(row["kinds"]) >= 3 and row["covered"] >= 0.9 * row["n1"]
    assert tb["I-04"]["kinds"] == ["key_family", "product_family", "procedure_relation"]
    assert "I-12" not in tb


def test_kinds_and_section_max():
    s = Settings()
    assert case3.order_kinds(T, "I-04", "family") == T["table"]["I-04"]["kinds"]  # key_family 이미 있음
    assert case3.order_kinds(T, "I-04", "product")[-1] == "product_unit"
    assert case3.order_kinds(T, None, "concern") == T["table"]["_all"]["kinds"]
    assert [s.first_section_max(i) for i in ("I-01", "I-02", "I-03", "I-04", "I-05", "I-06")] == [1, 2, 2, 2, 2, 1]
    _, sha = case3.order_table(s)
    assert len(sha) == 12
