"""모의 데이터 — 원장 21 · 시맨틱 사전(작은 고정 fixture · 가상 의학 문장 대신 「fixture」 표시 문자열).

레포에 원장 · 시맨틱 파일 이름을 두지 않으려고 파이썬 값으로만 들고, 시험 때 임시 폴더에 쓴다.
"""

from __future__ import annotations

import json
from pathlib import Path

from clinic_chat.quality.raw_tools import RAW_FILES

RAW_FIXTURE: dict[str, object] = {n: {"_fixture": f"fixture {n}"} for n in RAW_FILES}
RAW_FIXTURE.update({
    "taxonomy": {
        "modality": [
            {"id": "M2", "name": "보툴리눔 톡신(보톡스)", "note": "fixture 계열 설명"},
            {"id": "M9", "name": "HIFU 리프팅", "devices": ["듀얼파장(알렉+다이오드 or 다이오드+Nd:YAG)"]},
            {"id": "M3", "name": "필러(HA/CaHA)", "note": "fixture"},
        ],
        "problem": [
            {"id": "P2", "name": "주름", "sub": ["잔주름", "깊은주름"]},
            {"id": "P3", "name": "처짐", "note": "fixture 처짐 설명"},
            {"id": "P4", "name": "피부 처짐 Lifting", "note": "fixture"},
            {"id": "P5", "name": "fixture 문제 5"},
        ],
    },
    "procedure-profiles": {
        "M9": {"name": "소프웨이브", "aftercare": "fixture: 당일 사우나 피하기", "duration": "fixture 30분"},
        "M2": {"name": "이마 보톡스", "aftercare": "fixture: 4시간 눕지 않기"},
    },
    "contraindications": {"global": ["fixture: 임신 중 금기"], "M2": ["fixture: 신경근 질환"]},
    "products": {"botulinum_toxin": [{"name": "fixture 톡신 A"}], "hifu": [{"name": "10THERA"}]},
    "extracted": {"M9": {"overview": "fixture 초음파 리프팅 개요", "long": "가" * 300}},
})

SEMANTIC_FIXTURE = {
    "dictionary": {
        "resolve_rules": {
            "normalize": ["NFKC", "라틴 악센트 접기", "fold", "공백 제거", "괄호 지움", "전부 소문자"],
            "fold": {"쳐짐": "처짐"},
            "strip_chars": "()[]",
            "split": r"[\s/·,()\[\]+]+",
            "tails": ["주사", "시술"],
            "prefix_min": 2,
        },
        "expressions": [
            {"expr": "울쎄라", "variants": ["Ulthera", "울세라"], "targets": [{"id": "unit_procedure:U5-102"}]},
            {"expr": "소프웨이브", "variants": ["Sofwave"], "targets": [{"id": "unit_procedure:U5-140"}]},
            {"expr": "보톡스", "variants": ["보툴리눔"], "targets": [{"id": "family:M2"}]},
        ],
    }
}


def write_raw_dir(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    for n, v in RAW_FIXTURE.items():
        (d / f"{n}.json").write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def write_semantic(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(SEMANTIC_FIXTURE, ensure_ascii=False), encoding="utf-8")
    return p
