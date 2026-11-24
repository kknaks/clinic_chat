"""미리 만든 모달 효과 · 부작용 고객 문장(테스트 고침 B · 사용자 11-22 — `scripts/build_modal_text.py`).

- 파일 = `$J` 와 같은 폴더의 `modal-text-<$J meta.hub_gold_hash 앞 8>.json`(git 무시 자리). 키 = 케이스 코드.
- 읽기는 그 경로마다 한 번(프로세스 캐시 · 다시 읽기는 재시작 — JS1 과 같은 결). 파일이 없거나 meta 의 gold 가 다르면 없음 →
  모달은 지금 원문 꼴 그대로(오류 아님).
- 진료항목 설명 = `kos_description` 원문 · 비면 그 진료항목 첫 케이스의 `desc_line`(허브 시술 이름 · 부위 · 방법 이음).
"""

from __future__ import annotations

import json
from pathlib import Path

from clinic_chat.consult.kos_map import KosMap

_CACHE: dict[str, dict | None] = {}
CLASS_ORDER = (  # 묶음 순서(위급 맨 위) — 값은 파일의 class 원문(허브 severity_class)
    ("\uc704\uae09", "urgent"),  # 위급
    ("\uc8fc\uc758", "caution"),  # 주의
    ("\uc77c\uc2dc\uc801", "temporary"),  # 일시적
)
EFFECT = "\ud6a8\uacfc"  # 효과 항목 class 값


def path_for(kos: KosMap) -> Path | None:
    if not kos.path or not kos.hub_gold_hash:
        return None
    return Path(kos.path).parent / f"modal-text-{kos.hub_gold_hash[:8]}.json"


def load(kos: KosMap) -> dict | None:
    p = path_for(kos)
    if p is None:
        return None
    k = str(p)
    if k not in _CACHE:
        doc = None
        if p.is_file():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if str((d.get("meta") or {}).get("hub_gold_hash") or "") == kos.hub_gold_hash:
                    doc = d
            except ValueError:
                doc = None
        _CACHE[k] = doc
    return _CACHE[k]


def clear_cache() -> None:
    _CACHE.clear()


def desc_for(kos: KosMap, code: str) -> str:
    it = kos.item.get(code) or {}
    d = (it.get("kos_description") or "").strip()
    if d:
        return d
    doc = load(kos) or {}
    for c in (it.get("hub") or {}).get("case_codes") or []:
        line = ((doc.get("cases") or {}).get(c) or {}).get("desc_line") or ""
        if line:
            return line
    return ""


def view(kos: KosMap, code: str) -> dict | None:
    """모달 문장 값 — 진료항목 케이스들의 항목을 (분류, 이름) 하나로(먼저 케이스) · 효과 목록 + 부작용 묶음(위급 → 주의 → 일시적 →
    그 밖) · 칸(언제 · 얼마나 · 빈도)은 값 있는 것만. 파일에 그 진료항목 케이스가 하나도 없으면 None."""
    doc = load(kos)
    cases = (kos.item.get(code) or {}).get("hub", {}).get("case_codes") or []
    got = [((doc or {}).get("cases") or {}).get(c) for c in cases]
    got = [g for g in got if g is not None]
    if not doc or not got:
        return None
    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for g in got:
        for it in g.get("items") or []:
            key = (str(it.get("class") or ""), str(it.get("name") or ""))
            if key in seen or not key[1]:
                continue
            seen.add(key)
            rows.append({"name": key[1], "class": key[0], "sentence": str(it.get("sentence") or "").strip()})
    effects = [r for r in rows if r["class"] == EFFECT]
    sides = [r for r in rows if r["class"] != EFFECT]
    known = dict(CLASS_ORDER)
    groups = [{"cls": cls, "key": key, "rows": [r for r in sides if r["class"] == cls]} for cls, key in CLASS_ORDER] + [
        {"cls": "", "key": "none", "rows": [r for r in sides if r["class"] not in known]}
    ]
    return {"effects": effects, "groups": [g for g in groups if g["rows"]], "gold": doc["meta"].get("hub_gold_hash")}
