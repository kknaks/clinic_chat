"""시술 설명 모달 값(SPEC-010 §4-5 · MD1 ~ MD5 · §8 IM5 ~ IM9 · N3 · N4 · N5).

- `$J` 칸: kind · name · code · kind2 · site · drug · device · 게시 배지.
- 허브 칸(케이스마다 `facets` profile · outcome · adverse — 프로세스 캐시 (gold_hash, 노드, facet)):
  · lead · 원리 글 · 원리 코드 = 케이스 profile 응답의 **물려받은 시술 행**(N5) · 시술이 여럿이면 `hub.procedure_codes` 순서로 원리 하나씩
  · 방법 = 케이스 행 `method_codes`(` · `) · `method_texts`(`\\n`) — 같은 자리끼리 · 케이스 순서 → 안 순서 · 같은 코드 한 번(MD1 · IM6)
  · 효과 = outcome 행 가운데 `effect_code` 있는 것(N3 — 경과 줄은 `course` 로 따로 · LLM 몫)
  · 부작용 = adverse 행 가운데 `polarity` 「발생함」(N4 — 나머지는 `side_other`)
  · 모달 효과 · 부작용 정리(사용자 결정 11-22): **계열(modality)에서 물려받은 줄은 뺀다**(케이스 자기 · 시술 줄만 — `course` ·
    `side_other` · LLM 블록은 그대로) · **같은 이름은 하나로 합친다**(칸 값은 원문 그대로 모아 같은 값 한 번) · 부작용은 허브
    `severity_class` 로 위급 → 주의 → 일시적 → 빈칸 묶음 · 대처 줄은 `guidance_alerts` true 면 「바로 병원」 경고, 아니면 kind
    「관리」 만 대처로(경고 기준 · 모니터링 · 예방 = 관찰 기준 · 병원 쪽 줄이라 모달 0 · LLM 재료에는 그대로). 값 없는 칸은 비운다(지어 넣지 않음).
- 케이스 0(MD2) = 허브 칸 0 · 허브 오류(MD3) = 허브 칸 0 + `error`.
"""

from __future__ import annotations

from clinic_chat.consult import images as img
from clinic_chat.consult import modal_text as mt
from clinic_chat.consult.items_view import site_label
from clinic_chat.consult.kos_map import KosMap

SEP = " · "  # gold 이어 붙이기(` · ` — gold-build.py SEP)
OCCURS = "\ubc1c\uc0dd\ud568"  # 허브 polarity 값 「발생함」
MODALITY = "modality"  # 물려받은 줄의 holder 표(모달에서 뺌)
CLASSES = (  # 허브 severity_class 원문 → 묶음 키(위급이 맨 위)
    ("\uc704\uae09", "urgent"),  # 위급
    ("\uc8fc\uc758", "caution"),  # 주의
    ("\uc77c\uc2dc\uc801", "temporary"),  # 일시적
    ("", "none"),
)
CARE_KINDS = (
    "\uad00\ub9ac",
)  # 대처로 보일 guidance kind 원문 「관리」(경고 기준 · 모니터링 · 예방 = 관찰 기준 · 병원 쪽 줄 — 모달 0)
ALERT = "true"
_CACHE: dict[tuple[str, str, str], list] = {}


async def _rows(hub, gold: str, node: str, facet: str) -> list:
    k = (gold, node, facet)
    if k not in _CACHE:
        ra = await hub.read_all("facets", {"facet": facet, "node": node})
        if not ra.complete:
            raise RuntimeError(ra.error or "incomplete")
        _CACHE[k] = list(ra.items)
    return _CACHE[k]


def clear_cache() -> None:
    _CACHE.clear()


def _holder(r: dict) -> str:
    return str(((r.get("layer") or {}).get("holder") or {}).get("holder_table") or "")


def base(kos: KosMap, code: str) -> dict:
    it = kos.item[code]
    return {
        "code": code,
        "name": it.get("kos_item_name") or "",
        "kind": it.get("kos_procedure") or "",
        "site": site_label(it),
        "drug": it.get("kos_medicine") or "",
        "device": it.get("kos_machine") or "",
        "published": it.get("kos_status") == "PUBLISHED",
        "unit": it.get("kos_unit") or "",
        "desc": mt.desc_for(kos, code),  # 테스트 고침 B — 진료항목 설명(원문 · 비면 케이스 한 줄)
        "text": mt.view(kos, code),  # 테스트 고침 B — 미리 만든 고객 문장(없으면 None → 원문 꼴)
        "cases": list(it["hub"].get("case_codes") or []),
        "lead": "",
        "mechanisms": [],
        "methods": [],
        "effects": [],
        "sides": [],
        "side_groups": [],
        "counts": {},
        "course": [],
        "side_other": [],
        "has_hub": False,
        "error": None,
    }


async def build(kos: KosMap, hub, gold: str, code: str, image_dir: str | None) -> dict:
    """모달 값 하나. 그림은 코드 + 있음(요청마다 파일 — IM3 · IM8 · IM9)."""
    out = base(kos, code)
    cases = out["cases"]
    if not cases:  # MD2
        return out
    procs: dict[str, dict] = {}
    methods: list[dict] = []
    seen_m: set[str] = set()
    eff_rows: list[dict] = []
    side_rows: list[dict] = []
    try:
        for cs in cases:
            node = f"procedure_case:{cs}"
            prof = await _rows(hub, gold, node, "profile")
            for r in prof:
                if r.get("subject_table") == "procedure_case" and r.get("subject_code") == cs:
                    codes = [x for x in str(r.get("method_codes") or "").split(SEP) if x]
                    texts = str(r.get("method_texts") or "").split("\n") if r.get("method_texts") else []
                    for i, mc in enumerate(codes):
                        if mc in seen_m:
                            continue
                        seen_m.add(mc)
                        methods.append({"code": mc, "text": texts[i] if i < len(texts) else "", "case": cs})
                elif r.get("subject_table") == "procedure" and _holder(r) == "procedure":
                    procs.setdefault(str(r.get("subject_code")), r)
            for r in await _rows(hub, gold, node, "outcome"):
                ec = r.get("effect_code")
                if not ec:
                    out["course"].append(
                        {k: r.get(k) for k in ("onset_text", "peak_text", "duration_text", "note")} | {"case": cs}
                    )
                    continue
                eff_rows.append({**r, "_case": cs})
            for r in await _rows(hub, gold, node, "adverse"):
                ac = r.get("adverse_code") or ""
                if r.get("polarity") != OCCURS:
                    row = {
                        "code": ac,
                        "name": r.get("adverse_name") or "",
                        "severity": r.get("severity_text") or "",
                        "note": r.get("note") or "",
                        "case": cs,
                    }
                    out["side_other"].append({**row, "polarity": r.get("polarity")})
                    continue
                side_rows.append({**r, "_case": cs})
    except Exception as e:  # noqa: BLE001 — MD3 허브 칸만 비움
        out.update(
            lead="",
            mechanisms=[],
            methods=[],
            effects=[],
            sides=[],
            side_groups=[],
            course=[],
            side_other=[],
            error={"kind": type(e).__name__, "detail": str(e)[:200]},
        )
        return out
    order = list(kos.item[code]["hub"].get("procedure_codes") or [])
    order += [p for p in procs if p not in order]
    seen_mec: set[str] = set()
    for p in order:
        r = procs.get(p)
        if r is None:
            continue
        if not out["lead"] and r.get("overview"):
            out["lead"] = r["overview"]
        mc = r.get("mechanism_code") or ""
        key = mc or f"none:{p}"
        if key in seen_mec:
            continue
        seen_mec.add(key)
        out["mechanisms"].append(
            {
                "procedure": p,
                "code": mc,
                "name": r.get("mechanism_name") or "",
                "text": r.get("mechanism_text") or "",
                "present": img.present(image_dir, "mechanism", mc),
            }
        )
    for m in methods:
        m["present"] = img.present(image_dir, "method", m["code"])
    out["methods"] = methods
    out["effects"] = effects(eff_rows)
    out["sides"] = sides(side_rows)
    out["side_groups"] = side_groups(out["sides"])
    out["counts"] = {
        "effect_rows": len(eff_rows),
        "effect_before": len({r.get("effect_code") for r in eff_rows}),  # 옛 모달 = 코드마다 한 줄(계열 줄 포함)
        "effect_after": len(out["effects"]),
        "side_rows": len(side_rows),
        "side_before": len({r.get("adverse_code") or "" for r in side_rows}),
        "side_after": len(out["sides"]),
    }
    out["has_hub"] = True
    return out


def _add(vals: list[str], v: object) -> None:
    t = str(v or "").strip()
    if t and t not in vals:
        vals.append(t)


def _lines(r: dict, k: str) -> list[str]:
    v = r.get(k)
    return str(v).split("\n") if v else []


def effects(rows: list[dict]) -> list[dict]:
    """효과 — 계열 줄 뺌 · 같은 이름 하나 · 칸 = 언제부터(onset) · 최대(peak) · 얼마나(duration) 원문(같은 값 한 번)."""
    out: dict[str, dict] = {}
    for r in rows:
        if _holder(r) == MODALITY:
            continue
        name = str(r.get("effect_name") or r.get("effect_code") or "")
        e = out.setdefault(
            name, {"name": name, "codes": [], "cases": [], "onset": [], "peak": [], "duration": [], "note": ""}
        )
        _add(e["codes"], r.get("effect_code"))
        _add(e["cases"], r.get("_case"))
        for k in ("onset", "peak", "duration"):
            _add(e[k], r.get(f"{k}_text"))
        e["note"] = e["note"] or str(r.get("note") or "")
    for e in out.values():
        e["code"], e["case"] = e["codes"][0] if e["codes"] else "", e["cases"][0] if e["cases"] else ""
        e["parts"] = [{"f": k, "v": v} for k in ("onset", "peak", "duration") for v in e[k]]
    return list(out.values())


def sides(rows: list[dict]) -> list[dict]:
    """부작용 — 계열 줄 뺌 · 같은 이름 하나 · 칸 = 빈도 · 심한 정도 · 언제 · 얼마나 · 대처(관리) 원문 ·
    「바로 병원」 경고(`guidance_alerts` true 줄) · 분류 = 묶인 줄 가운데 가장 무거운 `severity_class`."""
    rank = {c: i for i, (c, _) in enumerate(CLASSES)}
    out: dict[str, dict] = {}
    for r in rows:
        if _holder(r) == MODALITY:
            continue
        name = str(r.get("adverse_name") or r.get("adverse_code") or "")
        e = out.setdefault(
            name,
            {
                "name": name,
                "codes": [],
                "cases": [],
                "cls": None,
                "freq": [],
                "severity": [],
                "onset": [],
                "duration": [],
                "care": [],
                "alerts": [],
                "note": "",
            },
        )
        _add(e["codes"], r.get("adverse_code"))
        _add(e["cases"], r.get("_case"))
        c = str(r.get("severity_class") or "")
        if e["cls"] is None or rank.get(c, len(rank)) < rank.get(e["cls"], len(rank)):
            e["cls"] = c
        _add(e["freq"], r.get("freq_grade_text"))
        _add(e["freq"], r.get("freq_value_text"))
        _add(e["severity"], r.get("severity_text"))
        _add(e["onset"], r.get("onset_text"))
        _add(e["duration"], r.get("duration_text"))
        texts, kinds, alerts = _lines(r, "guidance_texts"), _lines(r, "guidance_kinds"), _lines(r, "guidance_alerts")
        for i, t in enumerate(texts):
            if i < len(alerts) and alerts[i] == ALERT:
                _add(e["alerts"], t)
            elif i < len(kinds) and kinds[i] in CARE_KINDS:
                _add(e["care"], t)
        e["note"] = e["note"] or str(r.get("note") or "")
    for e in out.values():
        e["cls"] = e["cls"] or ""
        e["code"], e["case"] = e["codes"][0] if e["codes"] else "", e["cases"][0] if e["cases"] else ""
        e["care"] = [t for t in e["care"] if t not in e["alerts"]]
        e["parts"] = [{"f": k, "v": v} for k in ("freq", "severity", "onset", "duration", "care") for v in e[k]]
    return list(out.values())


def side_groups(items: list[dict]) -> list[dict]:
    """위급 → 주의 → 일시적 → 빈칸(분류 없음) 묶음 · 빈 묶음은 뺌 · 묶음 안 = 허브 순서."""
    keys = dict(CLASSES)
    groups = []
    for cls, key in CLASSES:
        its = [e for e in items if (e["cls"] if e["cls"] in keys else "") == cls]
        if its:
            groups.append({"cls": cls, "key": key, "rows": its})
    return groups


def figs(pm: dict) -> list[dict]:
    """IM12 — 장부 `modal.figs[]{table, code, present}`."""
    return [
        {"table": "mechanism", "code": m["code"], "present": m["present"]} for m in pm["mechanisms"] if m["code"]
    ] + [{"table": "method", "code": m["code"], "present": m["present"]} for m in pm["methods"]]
