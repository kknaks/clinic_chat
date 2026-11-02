"""case3 — 앱 서버: 허브 사전(T1) → Jev 재귀 탐색(peek → Choice → expand) → Sonnet 1회(SPEC-006 v2 §3-4).

① hub_resolve: T1 resolve(질문 전체 · 질문마다)
② jev#0: 의도 Choice 12 + 시작 노드 Choice(① 후보 · 비면 vocab 계열 · 고민 · 부위 · 「해당 없음」)
③ 걸음 1(v2.4): hub_peek#1 → hub_list#1-j(표 종류 중 앞 4 밖) → jev#1(이웃 Choice + 짝 Noul · 섹션 Choice · 「충분」 없음)
   → hub_expand#1(이웃 slim → 섹션 하나씩 · 짧은 fact)
④ 걸음 2..N: 대기열(고른 이웃 · 계열 · 제품 S 의 by_unit 단위) · 비면 S 로 돌아옴(peek 재사용) · 넘는 섹션은 빼고 계속
   멈춤 = enough(fact ≥ 1) · steps · budget(남은 예산 < 섹션 · 이웃 없음) · empty
⑤ hub_full#n: cut 줄 원문(출처 노드마다 expand(fact_ids) · WALK_FULL_BYTE_BUDGET)
⑥ claude: Sonnet 1회 · 도구 없음 · 입력 = §3-4-7 꼴(facts rank 순 · 출처 노드 · 인용 가능 id · 안전 요약)
되돌아가기(시작 노드 없음 · 첫 peek 실패 · 모은 fact 0) = v1 경로 1회(`fallback_*` — 의도 → 골드 도구 경로 · 의도 묶음).
WALK_MAX_STEPS=0 이면 v1 경로만(되돌림 스위치).
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from typesafe_sdk import Choice, Noul, TypeSafeError

from clinic_chat import prompts
from clinic_chat.cases.loop import cap_text, case_cost, new_case_result, node_ids
from clinic_chat.clients.anthropic_client import answer_params, stream_once, usage_dict
from clinic_chat.clients.jev_client import CHOICE_MAX, choice_stats
from clinic_chat.deps import NO_KEY, Deps
from clinic_chat.llm import CLI_PROVIDERS, cli
from clinic_chat.llm.specs import answer_spec
from clinic_chat.pricing import cost_usd
from clinic_chat.timing import CaseTimer

VOCAB_SLOTS = ("concern", "site", "purpose", "check_condition", "technique", "family", "menu")
PROC_KINDS = ("unit_procedure", "product")
NONE = "해당 없음"
NOUL_MIN = 0.5  # R-6 · 짝 Noul
SECTIONS_MAX = 4  # 「필요한 정보」 최대 칸 수 — 확률 높은 순(10-30 실물: 9칸 → 카드가 커져 잘림)
# 시술 2개가 필요한 의도 — 필요한 시술 수(I-07 은 1개면 자기 쌍)
TWO_PROC = {"I-06": 2, "I-07": 1, "I-08": 2}
SLOT_LABEL = {"family_a": "첫 번째로 말한 시술 계열", "family_b": "두 번째로 말한 시술 계열(하나만 말했으면 해당 없음)",
              "procedure_a": "첫 번째로 말한 시술", "procedure_b": "두 번째로 말한 시술","concern": "고민", "site": "부위", "purpose": "목적", "check_condition": "확인 조건", "technique": "기법",
              "family": "시술 계열", "menu": "메뉴(추천 패키지 라벨)", "procedure": "시술 · 제품"}
CARD_INTENTS = ("I-02", "I-03", "I-04", "I-05", "I-10")  # 시술 카드(T3) 의도 — 어느 칸을 받을지는 Jev 가 고름
# 「필요한 정보」 — Jev 가 질문마다 고르는 카드 칸(SPEC-005 §4-3 section 이름 · 안전 요약은 허브가 늘 줌)
INFO_SECTIONS = {
    "overview": "시술 개요(무엇인지)", "method": "시술 방법 · 원리", "recovery": "회복 · 다운타임 · 효과 지속",
    "aftercare": "시술 후 관리 · 주의(사우나 · 운동 등)", "timeline": "시술 후 경과 날짜별", "emergency": "바로 병원에 와야 하는 증상",
    "disclosure": "필수 고지 사항", "safety.side_effects": "부작용", "safety.complications": "합병증 · 드문 위험",
    "safety.pih": "색소 침착(PIH) 위험", "safety.management": "합병증 · 부작용 관리 · 예방", "checks": "해도 되는지 확인할 조건(임신 · 질환 · 복용약 등)",
    "site_caution": "부위별 주의", "dose": "용량 · 회차 · 간격", "products": "쓰는 제품 · 브랜드", "evidence": "근거 · 연구",
    "ops": "소요 시간 · 마취", "market": "시장 정보",
}
# 의도 Choice 설명의 예시 질문 — 허브 vocab 의도에 예시가 없을 때(gold-research §3 표의 예시 발화)
INTENT_EXAMPLES = {
    "I-01": ["이마 주름이 신경 쓰여요", "턱선이 처졌어요"], "I-02": ["소프웨이브는 어떻게 하는 거예요?"],
    "I-03": ["끝나고 사우나 가도 돼요?"], "I-04": ["멍 들어요?", "부작용 있어요?"],
    "I-05": ["바로 출근해도 돼요?", "얼마나 가요?"], "I-06": ["울쎄라랑 소프웨이브 뭐가 달라요?"],
    "I-07": ["오늘 셋 다 같이 해도 돼요?", "뭐부터 해요?"], "I-08": ["6주 전에 보톡스 맞았는데 괜찮아요?"],
    "I-09": ["임신 준비 중인데요", "켈로이드 체질이에요"], "I-10": ["필러 몇 cc 맞아요?", "볼루마로 해 주세요"],
    "I-11": ["같이 하면 좋은 거 있어요?"], "I-12": ["얼마예요?"],
}
_HANGUL = re.compile(r"[가-힣]")
_DAYS = re.compile(r"(\d+)\s*(일|주|개월|달)")


def _desc(item: dict) -> str:
    name = item.get("name") or item.get("label") or item["id"]
    syn = [s for s in (item.get("synonyms") or []) if _HANGUL.search(s)][:3]  # 한국어만(Q-8)
    return name + (" · " + ", ".join(syn) if syn else "")


def _shuffled(rng: random.Random, criteria: dict[str, str]) -> dict[str, str]:
    keys = list(criteria)
    rng.shuffle(keys)
    return {k: criteria[k] for k in keys}


def resolve_candidates(resolved: dict) -> tuple[list[dict], list[dict]]:
    cands, menus, seen = [], [], set()
    for m in resolved.get("matches", []):
        for c in m.get("candidates", []):
            if c["id"] not in seen:
                seen.add(c["id"])
                cands.append({**c, "span": m.get("span")})
        for c in m.get("menu_candidates", []):
            if c["id"] not in seen:
                seen.add(c["id"])
                menus.append({**c, "span": m.get("span")})
    return cands, menus


def build_jev_questions(vocab: dict, cands: list[dict], seed: int) -> tuple[dict, dict]:
    """Jev 질문 묶음 + 기록용 메타(선택지 수 · 줄임 여부 · 순서)."""
    rng = random.Random(seed)
    v = vocab.get("vocab", vocab)
    qs: dict[str, Any] = {}
    meta: dict[str, dict] = {}

    intents = {i["id"]: f"{i['name']} · 예: {' / '.join(i.get('examples') or INTENT_EXAMPLES.get(i['id'], []))}"
               for i in v.get("intent", [])}
    qs["intent"] = Choice(instructions="이 상담 질문의 의도는 무엇인가?", criteria=_shuffled(rng, intents))
    meta["intent"] = {"options_n": len(intents), "order": list(qs["intent"].criteria)}

    slot_items: dict[str, list[dict]] = {k: list(v.get(k, [])) for k in VOCAB_SLOTS}
    slot_items["procedure"] = [c for c in cands if c.get("kind") in PROC_KINDS]
    for slot, items in slot_items.items():
        reduced = False
        if len(items) + 1 > CHOICE_MAX:  # 255 넘으면 ① 후보로 줄임(Q-4)
            ids = {c["id"] for c in cands}
            items = [x for x in items if x["id"] in ids]
            reduced = True
        meta[slot] = {"options_n": len(items), "reduced": reduced}
        if not items:
            continue
        crit = {x["id"]: _desc(x) for x in items}
        crit[NONE] = "질문이 이 종류의 값을 말하지 않음"
        qs[f"slot:{slot}"] = Choice(instructions=f"질문이 말하는 {SLOT_LABEL[slot]}은(는)?", criteria=_shuffled(rng, crit))
        qs[f"said:{slot}"] = Noul(instructions=f"질문이 {SLOT_LABEL[slot]} 중 하나라도 말하나?")
        meta[slot]["order"] = list(qs[f"slot:{slot}"].criteria)
    for sec, desc in INFO_SECTIONS.items():  # 필요한 정보(같은 요청 · 병렬)
        qs[f"info:{sec}"] = Noul(instructions=f"이 질문에 답하려면 「{desc}」 정보가 필요한가?")
    fam_items = list(v.get("family", []))
    fam_reduced = len(fam_items) + 1 > CHOICE_MAX
    if fam_reduced:  # 255 넘으면 ① 후보로 줄임(다른 슬롯과 같음)
        ids = {c["id"] for c in cands}
        fam_items = [x for x in fam_items if x["id"] in ids]
    fams = {x["id"]: _desc(x) for x in fam_items}
    for slot in ("family_a", "family_b"):  # 시술 2개 의도용 — 계열 둘(실물 27 · 같은 요청)
        if fams:
            crit = {**fams, NONE: "질문이 이 값을 말하지 않음"}
            qs[f"slot:{slot}"] = Choice(instructions=f"질문이 말하는 {SLOT_LABEL[slot]}은(는)?", criteria=_shuffled(rng, crit))
            meta[slot] = {"options_n": len(fams), "reduced": fam_reduced, "order": list(qs[f"slot:{slot}"].criteria)}
    for c in cands:  # 여러 고민(R-6)
        if c.get("kind") == "concern":
            qs[f"concern:{c['id']}"] = Noul(instructions=f"질문이 「{c.get('name', c['id'])}」 고민을 말하나?")
    return qs, meta


def read_jev(resp: Any, meta: dict, conf_min: float) -> dict:
    out: dict[str, Any] = {"model": resp.model, "usage": {"input_tokens": resp.usage.input_tokens,
                                                         "output_tokens": resp.usage.output_tokens},
                           "slots": {}, "noul": {}}
    for name, a in resp.choices.items():
        p_max, ratio = choice_stats(dict(a.probabilities))
        row = {"choice": a.choice, "confidence": round(a.confidence, 4), "p_max": round(p_max, 4), "ratio": ratio,
               "low_confidence": a.confidence < conf_min}
        if name == "intent":
            out["intent"] = {**row, **meta["intent"]}
        else:
            slot = name.split(":", 1)[1]
            said = resp.nouls.get(f"said:{slot}")
            row["said"] = round(said.noul, 4) if said else None
            row["value"] = None if a.choice == NONE or (said is not None and said.noul < NOUL_MIN) else a.choice
            out["slots"][slot] = {**row, **{k: x for k, x in meta[slot].items() if k != "order"}}
    for slot, m in meta.items():  # 선택지가 없어 묻지 않은 슬롯도 기록
        if slot != "intent" and slot not in out["slots"]:
            out["slots"][slot] = {"value": None, "skipped": True, **{k: x for k, x in m.items() if k != "order"}}
    out["info"] = {n.split(":", 1)[1]: round(a.noul, 4) for n, a in resp.nouls.items() if n.startswith("info:")}
    picked = sorted((sec for sec in INFO_SECTIONS if out["info"].get(sec, 0) >= NOUL_MIN),
                    key=lambda sec: -out["info"][sec])  # 확률 높은 순(같으면 INFO_SECTIONS 순 — sorted 는 안정)
    out["sections"] = picked[:SECTIONS_MAX]
    out["sections_dropped"] = picked[SECTIONS_MAX:]
    for name, a in resp.nouls.items():
        if name.startswith("concern:"):
            out["noul"][name.split(":", 1)[1]] = round(a.noul, 4)
    return out


def _val(jev: dict, slot: str) -> str | None:
    """Jev 가 vocab · ① 후보 안에서 고른 값 그대로 — confidence < 문턱(low_confidence)이면 없음(되묻기)."""
    row = jev["slots"].get(slot) or {}
    return None if row.get("low_confidence") else row.get("value")


def _low(jev: dict, slot: str) -> bool:
    row = jev["slots"].get(slot) or {}
    return bool(row.get("low_confidence")) and row.get("choice") not in (None, NONE)


def fallback_plan_path(intent: str, jev: dict, cands: list[dict], menus: list[dict], question: str,
              procs: list[str] | None = None) -> dict:
    """의도 → 도구 경로(SPEC-005 §6-1 · SPEC-006 §3-4). 필수 슬롯 없으면 tool=None + missing.

    T1 후보는 255 넘는 슬롯 줄이기 · 시술 슬롯 판단용일 뿐 거르기가 아니다 — Jev 가 고른 슬롯 값은 T1 후보가 비어도
    그대로 쓴다(10-30 코디 승인). Jev 가 「해당 없음」이면 T1 후보로 채우고, 낮은 신뢰면 되묻기.
    """
    units = procs if procs is not None else [c["id"] for c in cands if c.get("kind") == "unit_procedure"]
    proc = _val(jev, "procedure") or _val(jev, "family")
    concerns = []
    if _val(jev, "concern"):
        concerns.append(_val(jev, "concern"))
    concerns += [cid for cid, p in jev["noul"].items() if p >= NOUL_MIN and cid not in concerns]
    if not concerns and not _low(jev, "concern"):
        concerns = [c["id"] for c in cands if c.get("kind") == "concern"]
    site = _val(jev, "site")
    if site is None and not _low(jev, "site"):
        site = next((c["id"] for c in cands if c.get("kind") == "site"), None)

    def need(*missing: str) -> dict:
        return {"intent": intent, "tool": None, "input": None, "missing": list(missing)}

    if intent == "I-12":
        return {"intent": intent, "tool": None, "input": None, "price_unavailable": True}
    if intent == "I-01":
        return {"intent": intent, "tool": "recommend", "input": {"concerns": concerns, "site": site}} if concerns \
            else need("concern")
    if intent in CARD_INTENTS:  # 칸(sections)은 run() 이 Jev 「필요한 정보」로 붙임 — 없으면 기본 짧은 카드
        return {"intent": intent, "tool": "procedure_card", "input": {"id": proc}} if proc else need("procedure")
    if intent == "I-06":
        ps = list(dict.fromkeys(units + ([proc] if proc and proc.startswith("unit_procedure:") else [])))[:4]
        return {"intent": intent, "tool": "compare", "input": {"procedures": ps}} if len(ps) >= 2 \
            else need("procedures(2+)")
    if intent == "I-07":
        ps = list(dict.fromkeys(units))[:6]
        if len(ps) == 1:
            ps = ps * 2  # 자기 쌍(재시술 간격) = 같은 id 두 번
        return {"intent": intent, "tool": "pair_check", "input": {"procedures": ps}} if ps else need("procedures")
    if intent == "I-08":
        m = _DAYS.search(question)
        if len(units) < 2 or not m:
            return need(*(["procedures(이전 · 지금)"] if len(units) < 2 else []), *([] if m else ["elapsed_days"]))
        n, u = int(m.group(1)), m.group(2)
        days = n * {"일": 1, "주": 7, "개월": 30, "달": 30}[u]
        order = sorted(units[:2], key=lambda i: question.find(next((c["span"] for c in cands if c["id"] == i), "")))
        return {"intent": intent, "tool": "pair_check",
                "input": {"procedures": order, "prior": {"procedure": order[0], "elapsed_days": days}}}
    if intent == "I-09":
        if not proc:
            return need("procedure")
        cond = _val(jev, "check_condition")
        if cond:
            return {"intent": intent, "tool": "check_conditions", "input": {"id": proc, "conditions": [cond]}}
        secs = jev.get("sections") or ["overview", "ops"]  # 확인 조건 노드 없음 → 카드(SPEC-005 §6-1)
        return {"intent": intent, "tool": "procedure_card", "input": {"id": proc, "sections": secs}}
    if intent == "I-11":
        label = _val(jev, "menu") or (menus[0]["id"] if menus else None)
        if label:
            return {"intent": intent, "tool": "combos", "input": {"menu": label.split(":", 1)[-1]}}
        if concerns:
            return {"intent": intent, "tool": "combos", "input": {"concern": concerns[0]}}
        p = _val(jev, "purpose")
        if p:
            return {"intent": intent, "tool": "combos", "input": {"purpose": p}}
        if proc and proc.startswith("unit_procedure:"):
            return {"intent": intent, "tool": "combos", "input": {"procedure": proc}}
        return need("concern|purpose|procedure|menu")
    return need("intent")


async def family_units(deps: Deps, fam: str) -> list[dict]:
    """계열의 단위 시술 [{id, name}] — 허브 procedure_card(family).card.units(앱 안 캐시)."""
    cache = deps.startup.setdefault("family_units", {})
    if fam not in cache:
        card = (await deps.hub_rest.call("procedure_card", {"id": fam})).get("card") or {}
        cache[fam] = [u for u in card.get("units") or [] if isinstance(u, dict) and u.get("id")]
    return cache[fam]


async def two_procs(deps: Deps, question: str, intent: str, jev: dict, cands: list[dict], seed: int,
                    timer: CaseTimer, trace: dict, tokens: dict) -> list[str]:
    """시술 2개 의도(I-06 · 07 · 08) — ① 후보 단위 시술 · 제품 → 모자라면 Jev 가 고른 계열(+부위)의 단위 시술을
    procedure_a · procedure_b 두 Choice(한 요청 · jev#2)로 → 그래도 모자라면 계열 · 제품 id 그대로(허브가 펼침)."""
    procs = [c["id"] for c in cands if c.get("kind") in PROC_KINDS]
    fams = [x for x in (_val(jev, "family_a"), _val(jev, "family_b"), _val(jev, "family")) if x]
    fams += [c["id"] for c in cands if c.get("kind") == "family"]
    fams = list(dict.fromkeys(fams))[:2]
    need = max(TWO_PROC[intent], min(2, len(fams)))  # 계열을 둘 말했으면 시술도 둘(I-07 「A 랑 B 같이」)
    unpicked: list[str] = []  # 단위 시술을 못 고른 계열 — 모자라면 계열 id 로
    site = _val(jev, "site")
    site_name = next((x.get("name") for x in deps.vocab.get("vocab", deps.vocab).get("site", []) if x["id"] == site), None)
    info: dict[str, Any] = {"from_resolve": list(procs), "families": fams}
    if len(dict.fromkeys(procs)) < need and fams:
        with timer.stage("hub_units"):
            lists = [await family_units(deps, f) for f in fams]
        qs: dict[str, Any] = {}
        rng = random.Random(seed + 1)
        slot_fam = dict(zip(("procedure_a", "procedure_b"), fams if len(fams) == 2 else fams * 2))
        for slot, units in zip(("procedure_a", "procedure_b"), lists if len(lists) == 2 else lists * 2):
            if site_name:  # 부위로 좁힘(이름에 부위가 든 단위 시술 · 없으면 그 계열 전부)
                units = [u for u in units if site_name in u.get("name", "")] or units
            units = units[:CHOICE_MAX - 1]
            if not units:
                continue
            crit = {u["id"]: u.get("name") or u["id"] for u in units}
            crit[NONE] = "질문이 이 시술을 말하지 않음"
            qs[f"slot:{slot}"] = Choice(instructions=f"질문이 말하는 {SLOT_LABEL[slot]}은(는)?", criteria=_shuffled(rng, crit))
            info[f"{slot}_options_n"] = len(units)
        if qs:
            try:
                with timer.stage("jev#2") as st:
                    resp = await deps.jev.system_one(state=question, questions=qs)
                    st["tokens"] = {"input_tokens": resp.usage.input_tokens}
                    st["cost_usd"] = cost_usd(deps.settings.typesafe_default_model, st["tokens"])
                trace["jev_calls"] = trace.get("jev_calls", 1) + 1
                tokens["jev#2"] = st["tokens"]
                for name, a in resp.choices.items():
                    slot = name.split(":", 1)[1]
                    low = a.confidence < deps.settings.jev_confidence_min
                    info[slot] = {"choice": a.choice, "confidence": round(a.confidence, 4), "low_confidence": low}
                    if a.choice != NONE and not low:
                        procs.append(a.choice)
                    else:
                        unpicked.append(slot_fam[slot])
            except TypeSafeError as e:  # 2단 실패 — 계열 id 로 이어 감
                info["error"] = _jev_error(e)
    procs = list(dict.fromkeys(procs))
    if len(procs) < need:  # 그래도 비면 계열 · 제품 id 로(허브가 펼침) — 못 고른 계열 먼저
        procs = list(dict.fromkeys(procs + unpicked + fams))[:max(need, len(procs))]
        info["fallback_ids"] = True
    info["procedures"] = procs
    trace["procedures"] = info
    return procs


def _jev_error(e: Exception) -> str:
    code = getattr(e, "status", None)
    return f"Jev 오류{f' {code}' if code else ''}: {type(e).__name__}"


async def fallback_path(deps: Deps, question: str, timer: CaseTimer, seed: int, trace: dict, tokens: dict,
                        resolved: dict | None = None) -> tuple[str | None, str | None]:
    """v1 경로 ①~③(SPEC-006 §3-4-5) — (Sonnet 에 줄 글, Jev 오류). 걸음에서 되돌아오면 ① resolve 결과를 그대로 받음."""
    if resolved is None:
        with timer.stage("hub_resolve"):
            resolved = await deps.hub_rest.resolve(question)
    cands, menus = resolve_candidates(resolved)
    trace["hub"] = {"resolve": {"matches": len(resolved.get("matches", [])),
                                "candidates": [c["id"] for c in cands], "menu_candidates": [m["id"] for m in menus],
                                "unmatched": resolved.get("unmatched", [])}}
    trace["gold_build_hash"] = (resolved.get("build") or {}).get("build_hash")

    qs, meta = build_jev_questions(deps.vocab, cands, seed)
    try:
        with timer.stage("jev") as st:
            resp = await deps.jev.system_one(state=question, questions=qs)
            st["tokens"] = {"input_tokens": resp.usage.input_tokens}
            st["cost_usd"] = cost_usd(deps.settings.typesafe_default_model, st["tokens"])
    except TypeSafeError as e:
        return None, _jev_error(e)
    trace["jev_calls"] = 1
    tokens["jev"] = st["tokens"]
    jev = read_jev(resp, meta, deps.settings.jev_confidence_min)
    jev["option_order_seed"] = seed
    trace["jev"] = jev

    intent = (jev.get("intent") or {}).get("choice")
    procs = None
    if intent in TWO_PROC:
        procs = await two_procs(deps, question, intent, jev, cands, seed, timer, trace, tokens)
    path = fallback_plan_path(intent, jev, cands, menus, question, procs)
    if path["tool"] in deps.intent_tools and intent:  # 허브 v3.1: 의도별 섹션 묶음 · 동봉(허브 기본 + Jev sections 합집합)
        path["input"] = {**path["input"], "intent": intent}
    if path["tool"] in deps.section_tools and jev["sections"] and "sections" not in (path["input"] or {}):
        path["input"] = {**path["input"], "sections": jev["sections"]}  # Jev 가 고른 칸만
    hub_result = None
    with timer.stage("hub_path"):
        if path["tool"]:
            hub_result = await deps.hub_rest.call(path["tool"], path["input"])
    trace["hub"].update({"path": intent, "tool": path["tool"], "input": path["input"],
                         "missing": path.get("missing"), "node_ids": node_ids(hub_result) if hub_result else []})

    slot_table = {k: {x: v[x] for x in ("value", "choice", "confidence", "low_confidence") if x in v}
                  for k, v in jev["slots"].items()}
    payload = {"intent": jev.get("intent"), "slots": slot_table}
    if path.get("missing"):
        payload["needs_clarification"] = {"missing": path["missing"]}
    if path.get("price_unavailable"):
        payload["price_unavailable"] = True
    hub_json = "(허브 호출 없음)" if hub_result is None else json.dumps(hub_result, ensure_ascii=False)
    hub_text, cut = _cap(deps, hub_json, trace.setdefault("_provider", "sdk"))
    trace["hub"].update({"result_bytes": len(hub_json.encode("utf-8")), "input_cap_bytes": _cap_of(deps, trace["_provider"])})
    if cut:
        trace["hub"]["truncated_to"] = _cap_of(deps, trace["_provider"])
    return (f"질문: {question}\n\n[앱이 해석한 의도 · 슬롯 표]\n{json.dumps(payload, ensure_ascii=False)}"
            f"\n\n[허브 결과]\n{hub_text}"), None


def _cap_of(deps: Deps, provider: str) -> int | None:
    """입력 상한은 세 케이스 같게 — sdk 판: TOOL_RESULT_MAX_BYTES(0 = 없음) / CLI 판: 넣을 자리 없음."""
    return (deps.settings.tool_result_max_bytes or None) if provider == "sdk" else None


def _cap(deps: Deps, text: str, provider: str) -> tuple[str, bool]:
    cap = _cap_of(deps, provider)
    return cap_text(text, cap) if cap else (text, False)


async def _answer(deps: Deps, user: str, timer: CaseTimer, on_text: Callable[[str], None], provider: str,
                  res: dict, tokens: dict, trace: dict) -> dict | None:
    """④ Sonnet 1회(도구 없음) — CLI 판이면 CLI 결과(timer 를 CLI 가 끝냄)를 돌려줌."""
    if provider in CLI_PROVIDERS:  # ④ 를 CLI 로(도구 없음 · MCP 안 붙임) — 앞 단계는 그대로 앱
        cli_res = await cli.run(answer_spec(deps.settings, provider), system=prompts.system_for("case3"),
                                prompt=user, timer=timer, on_text=on_text)
        for k in ("answer", "stop_reason", "refusal", "error", "structured"):
            res[k] = cli_res.get(k)
        tokens["llm"] = cli_res["tokens"]
        trace["cli"] = cli_res["trace"]["cli"]
        return cli_res
    params = answer_params(deps.settings.answer_model, prompts.system_for("case3"),
                           [{"role": "user", "content": user}], None)
    first: list[float] = []

    def _txt(t: str) -> None:
        ms = timer.mark_text()
        if not first:
            first.append(ms)
        res["answer"] += t
        on_text(t)

    with timer.stage("claude") as st:
        final = await stream_once(deps.anthropic, params, _txt)
        st["tokens"] = usage_dict(final.usage)
        st["cost_usd"] = cost_usd(deps.settings.answer_model, st["tokens"])
    timer.llm_round_trips += 1
    tokens["claude"] = st["tokens"]
    timer.ttft_answer_ms = first[0] if first else None
    res["stop_reason"] = final.stop_reason
    res["refusal"] = final.stop_reason == "refusal"
    return None


def _finish(res: dict, timer: CaseTimer, cli_res: dict | None) -> None:
    res["trace"].pop("_provider", None)
    if cli_res is not None:  # CLI 가 timer 를 끝냄 — cli_boot · ttft_kind 칸 그대로
        res["timing"] = cli_res["timing"]
        cost = case_cost(timer.stages)  # Jev(API)
        est = cli_res["cost"].get("usd_estimate")
        cost.update({"billed": "subscription", "usd_estimate": est, "usd": round(cost["usd"] + (est or 0.0), 6)})
        res["cost"] = cost
    else:
        timer.finish()
        res["timing"] = timer.as_dict()
        res["cost"] = case_cost(timer.stages)


async def fallback_run(deps: Deps, question: str, timer: CaseTimer, on_text: Callable[[str], None], seed: int = 0,
                       provider: str = "sdk") -> dict:
    """v1 case3 그대로(걸음 없음 — WALK_MAX_STEPS=0 되돌림 스위치)."""
    res = new_case_result()
    cli_res: dict | None = None
    if deps.jev is None or (provider == "sdk" and deps.anthropic is None):
        res["error"] = NO_KEY
        return res
    trace: dict[str, Any] = {"hub_list": "cache" if deps.vocab is not None else "load", "_provider": provider}
    res["trace"] = trace
    tokens: dict[str, dict] = {}
    res["tokens"] = tokens
    try:
        if deps.vocab is None:
            deps.vocab = await deps.hub_rest.vocab()
        user, err = await fallback_path(deps, question, timer, seed, trace, tokens)
        if err:
            res["error"] = err
            return res
        cli_res = await _answer(deps, user, timer, on_text, provider, res, tokens, trace)
    except Exception as e:  # noqa: BLE001 — 허브 · Sonnet 오류 — 그 열만 오류
        res["error"] = f"{type(e).__name__}: {e}"[:500]
    finally:
        _finish(res, timer, cli_res)
    return res


# ───────────── Jev 걸음(SPEC-006 v2.4 §3-4-1 · 보충 jev-walk) ─────────────
NONE_ENOUGH = "없음(충분)"
NO_SECTION = "없음"
START_KINDS = ("family", "concern", "site")  # ① 후보가 비면 시작 노드를 고르는 vocab 종류(Q-4)
SEC = "section:"
NO_WALK_INTENTS = ("I-12",)
PROC_NODE_KINDS = ("unit_procedure", "product", "family")
CHECK_EDGE_KINDS = ("requires_check", "requires_check_unit")  # 고침 4 A — 시술 → 확인 조건 이웃  # 걷기 고침 2 — 필수 묶음 · 고정 섹션을 받는 「시술」 노드  # 걸음 없음(허브 도구 없음 · v1 과 같게 「가격 정보 없음」 · §3-4-6 표)
ORDER_FILE = Path(__file__).with_name("walk_order.json")


@lru_cache(maxsize=4)
def _order_file(path: str) -> tuple[dict, str]:
    raw = Path(path).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()[:12]


def order_table(settings: Any) -> tuple[dict, str]:
    """걸음 1 이웃 순서 표(§3-4-6 · spec006-v24-recount.json order_v24 복사본) · 판 sha(파일 sha256 앞 12자)."""
    return _order_file(str(Path(settings.walk_order_table) if settings.walk_order_table else ORDER_FILE))


def order_kinds(table: dict, intent: str | None, s_kind: str | None) -> list[str]:
    """그 의도의 edge kind(행 없으면 전체 `_all`) + S 가 계열이면 key_family · 제품이면 product_unit(표에 없을 때 · 늘 더함)."""
    rows = table.get("table") or {}
    kinds = list((rows.get(intent or "") or rows.get("_all") or {}).get("kinds") or [])
    link = (table.get("unit_link") or {}).get(s_kind or "")
    if link and link not in kinds:
        kinds.append(link)
    return kinds


def _compact(o: Any) -> int:
    """허브 REST 본문과 같은 직렬화의 UTF-8 바이트(§3-4-2 · F-1 — 공백 없는 구분자)."""
    return len(json.dumps(o, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def walk_start_options(vocab: dict, cands: list[dict]) -> tuple[dict[str, str], str]:
    """시작 노드 선택지 — ① 후보(메뉴 빼고) · 비면 vocab 계열 · 고민 · 부위. 255 넘으면 앞에서 자름(기록)."""
    if cands:
        items, src = cands, "resolve"
    else:
        v = vocab.get("vocab", vocab)
        items, src = [{**x, "kind": k} for k in START_KINDS for x in v.get(k, [])], "vocab"
    crit = {x["id"]: f"{_desc(x)} ({SLOT_LABEL.get(x.get('kind', ''), x.get('kind', ''))})" for x in items}
    if len(crit) + 1 > CHOICE_MAX:
        crit = dict(list(crit.items())[:CHOICE_MAX - 1])
        src += "(255 자름)"
    return crit, src


def nb_label(name: str | None, nid: str, edge: str, label: str | None, n_facts: int | None) -> str:
    """이웃 선택지 라벨(W-10) — 이름 — edge · label · fact {n_facts}."""
    parts = [edge] + ([label] if label and label != edge else []) + ([f"fact {n_facts}"] if n_facts is not None else [])
    return f"{name or nid} — {' · '.join(parts)}"


def sec_label(sec: dict) -> str:
    """섹션 선택지 라벨(W-10) — 이름 · fact {n_facts} · {bytes}B."""
    nm = sec.get("name")
    return f"{nm}: {INFO_SECTIONS.get(nm, nm)} · fact {sec.get('n_facts')} · {sec.get('bytes')}B"


def section_options(peek: dict, taken: set[str], left: int) -> tuple[dict[str, str], list[str]]:
    """peek 섹션 → 선택지 · 남은 예산(left)보다 큰 섹션(peek bytes = 실제 expand 바이트)은 미리 뺌(filtered_by_budget)."""
    opts: dict[str, str] = {}
    filtered: list[str] = []
    for sec in peek.get("sections") or []:
        nm = sec.get("name")
        if not nm or nm in taken or not sec.get("n_facts", 1):
            continue
        if isinstance(sec.get("bytes"), int) and sec["bytes"] > left:
            filtered.append(nm)
            continue
        opts[SEC + nm] = sec_label(sec)
    return opts, filtered


def walk_options(peek: dict, seen: set[str], taken: set[str] | None = None,
                 left: int | None = None) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """걸음 2~ — 펼칠 것 선택지(남은 예산에 맞는 섹션 ∪ 이 노드의 이웃 id) · 이웃 → edge kind · 예산으로 뺀 섹션."""
    opts, filtered = section_options(peek, taken or set(), left if left is not None else 1 << 62)
    kind_of = {x: "section" for x in opts}
    for edge, grp in (peek.get("neighbors_by_kind") or {}).items():
        for it in grp.get("items") or []:
            i = it.get("id")
            if i and i not in seen and i not in opts:
                opts[i] = nb_label(it.get("name"), i, edge, it.get("label"), it.get("n_facts"))
                kind_of[i] = edge
    return opts, kind_of, filtered


def _state(question: str, node: dict, facts: list[dict]) -> str:
    """Jev state — 질문 · 지금 노드 · 지금까지 모은 fact 한 줄."""
    got = [f"- [{f.get('section')}] {f.get('line')}" for f in facts]
    return (f"질문: {question}\n\n지금 노드: {node.get('name')}({node.get('kind')})\n\n지금까지 모은 정보:\n"
            + ("\n".join(got) if got else "(없음)"))


def _choice_row(a: Any) -> dict:
    p_max, ratio = choice_stats(dict(a.probabilities))
    return {"choice": a.choice, "confidence": round(a.confidence, 4), "p_max": round(p_max, 4), "ratio": ratio}


def _also(resp: Any, prefix: str, keys: list[str], skip: str | None) -> list[str]:
    """짝 Noul ≥ NOUL_MIN 인 선택지 — 높은 순(같으면 선택지 순)."""
    got = sorted(((resp.nouls[f"{prefix}{n}"].noul, n) for n in range(len(keys))
                  if resp.nouls.get(f"{prefix}{n}") and resp.nouls[f"{prefix}{n}"].noul >= NOUL_MIN), key=lambda t: (-t[0], t[1]))
    return [keys[n] for _, n in got if keys[n] != skip]


async def _jev(deps: Deps, timer: CaseTimer, name: str, state: str, qs: dict, tokens: dict, walk: dict) -> Any:
    with timer.stage(name) as st:
        t = timer.clock.ms()
        resp = await deps.jev.system_one(state=state, questions=qs)
        st["tokens"] = {"input_tokens": resp.usage.input_tokens}
        st["cost_usd"] = cost_usd(deps.settings.typesafe_default_model, st["tokens"])
    ms = round(timer.clock.ms() - t, 1)
    tokens[name] = st["tokens"]
    walk["jev_calls"] += 1
    walk["jev_ms_total"] = round(walk["jev_ms_total"] + ms, 1)
    return resp, ms


async def _hub(deps: Deps, timer: CaseTimer, name: str, walk: dict, coro_fn) -> tuple[dict, int, float]:
    with timer.stage(name):
        t = timer.clock.ms()
        d = await coro_fn()
    ms = round(timer.clock.ms() - t, 1)
    b = _compact(d)
    walk["hub_calls"] += 1
    walk["hub_ms_total"] = round(walk["hub_ms_total"] + ms, 1)
    return d, b, ms


async def walk_pick(deps: Deps, timer: CaseTimer, k: int, question: str, peek: dict, opts: dict[str, str],
                    kind_of: dict[str, str], facts: list[dict], seed: int, tokens: dict, walk: dict) -> dict:
    """jev#k(걸음 2~) — Choice「펼칠 것」(+ 「없음(충분)」) · 선택지마다 짝 Noul · Noul「충분」. 255 넘으면 kind 를 먼저(2단)."""
    rng = random.Random(seed * 1000 + k)
    state = _state(question, peek.get("node") or {}, facts)
    enough_q = Noul(instructions="지금까지 모은 정보로 이 질문에 답하기에 충분한가?")
    two = len(opts) + 1 > CHOICE_MAX
    row: dict[str, Any] = {"options_n": len(opts), "two_stage": two}
    ms_total = 0.0
    usage = 0
    if two:  # 1단: 종류(섹션 · edge kind) Choice + 충분
        kinds = sorted(set(kind_of.values()))
        crit = {kd: ("섹션(이 노드의 fact 묶음)" if kd == "section" else f"이웃 — {kd}") for kd in kinds}
        crit[NONE_ENOUGH] = "더 펼칠 것 없음 — 지금까지로 충분"
        resp, ms = await _jev(deps, timer, f"jev#{k}", state, {"kind": Choice(
            instructions="이 질문에 답하려고 다음에 펼칠 종류는?", criteria=_shuffled(rng, crit)), "enough": enough_q},
            tokens, walk)
        ms_total += ms
        usage += resp.usage.input_tokens
        a = resp.choices["kind"]
        row["kind"] = _choice_row(a)
        row["enough"] = round(resp.nouls["enough"].noul, 4)
        if a.choice == NONE_ENOUGH:
            row.update({"picked": [], "ms": round(ms_total, 1), "usage": {"input_tokens": usage}})
            return row
        opts = {x: d for x, d in opts.items() if kind_of[x] == a.choice}
        opts = dict(list(opts.items())[:CHOICE_MAX - 1])
    crit = {**opts, NONE_ENOUGH: "더 펼칠 것 없음 — 지금까지로 충분"}
    qs: dict[str, Any] = {"pick": Choice(instructions="이 질문에 답하려고 다음에 펼칠 것은?", criteria=_shuffled(rng, crit))}
    if not two:
        qs["enough"] = enough_q
    keys = list(opts)
    for n, x in enumerate(keys):  # 짝 Noul — 여럿 고르기(R-6 과 같은 식)
        qs[f"also:{n}"] = Noul(instructions=f"이 질문에 답하려면 「{opts[x]}」 도 펼쳐 봐야 하나?")
    resp, ms = await _jev(deps, timer, f"jev#{k}" + ("-2" if two else ""), state, qs, tokens, walk)
    ms_total += ms
    usage += resp.usage.input_tokens
    a = resp.choices["pick"]
    row.update(_choice_row(a))
    if not two:
        row["enough"] = round(resp.nouls["enough"].noul, 4)
    picked = list(dict.fromkeys(([a.choice] if a.choice != NONE_ENOUGH else []) + _also(resp, "also:", keys, None)))
    s = deps.settings
    secs = [x for x in picked if x.startswith(SEC)][:s.walk_sections_per_step]
    nbs = [x for x in picked if not x.startswith(SEC)][:s.walk_neighbors_max]
    row.update({"picked": secs + nbs, "picked_sections": [x[len(SEC):] for x in secs], "picked_neighbors": nbs,
                "ms": round(ms_total, 1), "usage": {"input_tokens": usage}})
    return row


async def step1_neighbors(deps: Deps, timer: CaseTimer, walk: dict, node: str, peek: dict,
                          kinds: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """걸음 1 이웃 선택지 — 표의 edge kind 종류 순 · peek 앞 4 + n > 앞 4 인 종류는 expand(neighbor_kinds=[kind]) 목록 전부
    (hub_list#1-j · 예산 밖 · Sonnet 에 안 감). 표 종류에 이웃이 없으면 peek 종류 순(GOLD_PEEK_KIND_ORDER)의 다음 종류로 채움."""
    nbk = peek.get("neighbors_by_kind") or {}
    use = [x for x in kinds if (nbk.get(x) or {}).get("n")]
    if not use:
        fill = next((x for x, g in nbk.items() if g.get("n") and x not in kinds), None)
        use = [fill] if fill else []
        walk["order_fill"] = fill
    opts: dict[str, str] = {}
    kind_of: dict[str, str] = {}
    lst = walk["list"]
    for kd in use:
        grp = nbk[kd]
        items = grp.get("items") or []
        for it in items:
            i = it.get("id")
            if i and i != node and i not in opts:
                opts[i] = nb_label(it.get("name"), i, kd, it.get("label"), it.get("n_facts"))
                kind_of[i] = kd
        if (grp.get("n") or 0) <= len(items):
            continue
        j = lst["calls"] + 1
        try:
            ex, eb, ems = await _hub(deps, timer, f"hub_list#1-{j}", walk,
                                     lambda k_=kd: deps.hub_rest.expand(node, neighbor_kinds=[k_]))
        except Exception as e:  # noqa: BLE001 — 목록 실패는 앞 4 로 이어 감(기록)
            lst.setdefault("errors", []).append(f"{kd}: {type(e).__name__}: {e}"[:200])
            continue
        lst["calls"] = j
        lst["kinds"].append(kd)
        lst["bytes"] += eb
        lst["ms"] = round(lst["ms"] + ems, 1)
        before = len(opts)
        for r in ex.get("neighbors") or []:
            i = r.get("id") if isinstance(r, dict) else None
            if i and i != node and i not in opts:
                n_facts = sum((r.get("sections_n") or {}).values()) if isinstance(r.get("sections_n"), dict) else None
                opts[i] = nb_label(r.get("name"), i, r.get("kind_edge") or kd, None, n_facts)
                kind_of[i] = r.get("kind_edge") or kd
        for i in ex.get("neighbors_more_ids") or []:
            if i and i != node and i not in opts:
                opts[i] = nb_label(None, i, kd, None, None)
                kind_of[i] = kd
        lst["ids_n"] += len(opts) - before
    return opts, kind_of


async def walk_pick_first(deps: Deps, timer: CaseTimer, question: str, intent: str | None, peek: dict,
                          nb_opts: dict[str, str], kind_of: dict[str, str], sec_opts: dict[str, str], seed: int,
                          tokens: dict, walk: dict) -> dict:
    """jev#1 — Choice「먼저 볼 이웃」 + 짝 Noul · Choice「같이 볼 섹션」(+ 「없음」 · 상한 2 인 의도는 짝 Noul) — 한 요청 ·
    「충분」 은 묻지 않음. 이웃이 255 넘으면 1단(종류 + 섹션) → 2단(그 종류 이웃)."""
    s = deps.settings
    rng = random.Random(seed * 1000 + 1)
    state = _state(question, peek.get("node") or {}, [])
    sec_max = s.first_section_max(intent)
    sec_keys = list(sec_opts)
    two = len(nb_opts) > CHOICE_MAX
    row: dict[str, Any] = {"options_n": len(nb_opts) + len(sec_opts), "nb_options_n": len(nb_opts),
                           "sec_options_n": len(sec_opts), "sections_max": sec_max, "two_stage": two}
    qs: dict[str, Any] = {}
    if sec_opts:
        qs["sec"] = Choice(instructions="이 노드에서 같이 볼 섹션은?",
                           criteria=_shuffled(rng, {**sec_opts, NO_SECTION: "섹션은 지금 안 봄"}))
        if sec_max > 1:
            for n, x in enumerate(sec_keys):
                qs[f"sec_also:{n}"] = Noul(instructions=f"이 질문에 답하려면 「{sec_opts[x]}」 섹션도 봐야 하나?")
    nbs = dict(nb_opts)
    if two:
        kinds = list(dict.fromkeys(kind_of[x] for x in nb_opts))
        qs["kind"] = Choice(instructions="이 질문에 답하려고 먼저 볼 이웃 종류는?",
                            criteria=_shuffled(rng, {kd: f"이웃 — {kd}" for kd in kinds}))
    elif nbs:
        qs["nb"] = Choice(instructions="이 질문에 답하려고 먼저 볼 이웃은?", criteria=_shuffled(rng, nbs))
        for n, x in enumerate(nbs):
            qs[f"nb_also:{n}"] = Noul(instructions=f"이 질문에 답하려면 「{nbs[x]}」 도 봐야 하나?")
    ms_total, usage = 0.0, 0
    resp, ms = await _jev(deps, timer, "jev#1", state, qs, tokens, walk)
    ms_total += ms
    usage += resp.usage.input_tokens
    secs: list[str] = []
    if "sec" in resp.choices:
        a = resp.choices["sec"]
        row["sec"] = _choice_row(a)
        first = [a.choice] if a.choice != NO_SECTION else []
        secs = list(dict.fromkeys(first + (_also(resp, "sec_also:", sec_keys, None) if sec_max > 1 else [])))[:sec_max]
    if two:
        a = resp.choices["kind"]
        row["kind"] = _choice_row(a)
        nbs = dict(list({x: d for x, d in nb_opts.items() if kind_of[x] == a.choice}.items())[:CHOICE_MAX])
        qs2: dict[str, Any] = {"nb": Choice(instructions="이 질문에 답하려고 먼저 볼 이웃은?", criteria=_shuffled(rng, nbs))}
        for n, x in enumerate(nbs):
            qs2[f"nb_also:{n}"] = Noul(instructions=f"이 질문에 답하려면 「{nbs[x]}」 도 봐야 하나?")
        resp, ms = await _jev(deps, timer, "jev#1-2", state, qs2, tokens, walk)
        ms_total += ms
        usage += resp.usage.input_tokens
    picked_nb: list[str] = []
    if "nb" in resp.choices:
        a = resp.choices["nb"]
        row.update(_choice_row(a))
        picked_nb = list(dict.fromkeys([a.choice] + _also(resp, "nb_also:", list(nbs), a.choice)))[:s.walk_neighbors_max]
    elif "sec" in row:
        row.update({k: row["sec"][k] for k in ("choice", "confidence", "p_max", "ratio")})
    row.update({"picked": [SEC + x[len(SEC):] for x in secs] + picked_nb, "picked_sections": [x[len(SEC):] for x in secs],
                "picked_neighbors": picked_nb, "ms": round(ms_total, 1), "usage": {"input_tokens": usage}})
    return row


class Walker:
    """한 판의 걸음 상태 — 예산 · 대기열 · 모은 줄(rank 순) · peek 캐시 · 안전 요약 · 이웃 slim."""

    def __init__(self, deps: Deps, timer: CaseTimer, walk: dict, start: str) -> None:
        self.deps, self.timer, self.walk, self.start = deps, timer, walk, start
        self.s = deps.settings
        self.peeks: dict[str, tuple[dict, int, float]] = {}
        self.facts: list[dict] = []  # 걸음 순 → 호출 순 → 응답 facts[] 순(중복 없음)
        self.fact_seen: set[str] = set()
        self.safety: dict[str, Any] = {}
        self.cards: dict[str, dict] = {}
        self.flags: set[str] = set()
        self.rule: str | None = None
        self.queue: list[dict] = []
        self.seen: set[str] = {start}
        self.unit_seen: set[tuple[str, str]] = set()
        self.taken: dict[str, set[str]] = {}  # 노드 → 받은(또는 뺀) 섹션
        self.edge_of: dict[str, str] = {}  # 노드 → 고른 edge kind
        self.s_link: str | None = None  # S 가 계열 · 제품이면 단위 이웃 종류(key_family · product_unit)
        self.texts: dict[str, str] = {}  # ⑤ 로 받은 원문
        self.fixed_done = False  # 의도별 고정 섹션은 첫 시술 노드에서 한 번
        self.required_done: set[str] = set()
        self.bundle_done: set[str] = set()
        self.safety_first_done = False

    def kind(self, node: str) -> str | None:
        return ((self.peeks.get(node) or ({},))[0].get("node") or {}).get("kind")

    def bundle(self, intent: str | None) -> list[str]:
        """의도 → 섹션 기본 묶음(SPEC-005 §5-14 — 허브 vocab `intent[].sections` 그대로 · 걷기 고침 3 ①)."""
        v = (self.deps.vocab or {}).get("vocab", self.deps.vocab or {})
        return list(next((i.get("sections") or [] for i in v.get("intent") or [] if i.get("id") == intent), []))

    async def extras(self, k: int, node: str, step: dict, peek: dict, intent: str | None) -> None:
        """Jev 고름 전에 규칙으로 받는 섹션(예산 먼저 차감 · 하나씩 · 넘으면 미리 뺌):
        ① 필수 묶음(고침 2 ①) — 시작 노드가 시술이면 checks · site_caution
        ② 의도 묶음(고침 3 ① ② · WALK_BASE_BUNDLE) — 걷는 노드마다 그 의도의 §5-14 기본 섹션(시술 S · 첫 시술 노드 = 바닥)
        ③ 의도별 고정 섹션(고침 2 ③) — 첫 시술 노드에서 한 번.
        기록 walk.required · walk.base_bundle(첫 시술 노드) · walk.bundles[] · walk.fixed."""
        proc = self.kind(node) in PROC_NODE_KINDS
        items: list[tuple[str, str]] = []
        first_proc = proc and (node == self.start or (self.s.walk_safety_budget > 0 and not self.safety_first_done))
        if first_proc and node not in self.required_done:  # 시술 S · 아니면(고침 4) 처음 걷는 시술 노드
            items += self.safety_items(intent)
            self.required_done.add(node)
            self.safety_first_done = True
        bd = None
        if self.s.walk_base_bundle and node not in self.bundle_done:
            self.bundle_done.add(node)
            bd = self.bundle(intent)
            items += [(x, "bundle") for x in bd]
        fx = None
        if proc and not self.fixed_done and self.s.fixed_sections(intent):
            self.fixed_done = True
            fx = self.s.fixed_sections(intent)
            items += [(x, "fixed") for x in fx]
        if not items:
            return
        plan, filt = self.plan_extra(node, peek, items)
        if fx is not None:
            self.walk["fixed"] = {"node": node, "step": k, "want": fx, "sections": [x for x, w in plan if w == "fixed"],
                                  "filtered": [x for x in filt if x in fx]}
        if bd:
            row = {"node": node, "step": k, "want": bd, "sections": [x for x, w in plan if w == "bundle"],
                   "filtered": [x for x in filt if x in bd]}
            self.walk["bundles"].append(row)
            if proc and self.walk["base_bundle"] is None:
                self.walk["base_bundle"] = row
        if any(w == "required" for _, w in items):
            req = self.walk["required"]
            req["nodes"].append(node)
            req["filtered"] += [f"{node}|{x}" for x in filt if x in self.s.required_sections()]
        self._note_cut(node, [(x, w) for x, w in items if x in filt])
        if plan:
            got = await self.expand(k, node, step, peek, [], [], plan)
            self._note_required(node, step, got)

    def safety_items(self, intent: str | None) -> list[tuple[str, str]]:
        """필수 묶음(checks · site_caution · I-04 safety.management) + 안전 섹션(safety.side_effects · safety.complications —
        고침 4 · 안전 예산이 있을 때만)."""
        out = [(x, "required") for x in self.s.required_sections()]
        if self.s.walk_safety_budget > 0:
            if intent == "I-04":
                out.append(("safety.management", "required"))
            out += [(x, "safety") for x in self.s.safety_sections()]
        return out

    def pool(self, why: str) -> str:
        """고침 4 — 필수 · 안전 섹션은 안전 예산(WALK_SAFETY_BUDGET · 20KB 밖) · 그 밖은 일반 예산."""
        return "safety" if self.s.walk_safety_budget > 0 and why in ("required", "safety") else "general"

    def left_of(self, pool: str) -> int:
        if pool == "safety":
            return self.s.walk_safety_budget - self.walk["safety_budget"]["bytes"]
        return self.left

    def _note_cut(self, node: str, items: list[tuple[str, str]], bytes_: int | None = None) -> None:
        for x, why in items:
            if self.pool(why) == "safety":
                self.walk["safety_budget"]["cut"].append({"node": node, "section": x,
                                                          **({"bytes": bytes_} if bytes_ is not None else {})})

    async def fill(self, intent: str | None) -> None:
        """걷기 고침 3 ③ — 걸음 뒤 예산이 남으면 방문한 노드들의 아직 안 받은 섹션을 의도 순서(§5-14 묶음 순 → 그 밖은
        peek 섹션 순) · 방문 순으로 남은 예산이 찰 때까지(1쪽 · 결정론 · 넘는 섹션은 건너뛰고 다음). 기록 walk.fill."""
        order = self.bundle(intent)
        visit = list(dict.fromkeys(st["node"] for st in self.walk["steps"] if st.get("node") in self.peeks))
        cand = []
        for vi, n in enumerate(visit):
            secs = [x for x in self.peeks[n][0].get("sections") or [] if x.get("name") and x.get("n_facts", 1)]
            for pi, x in enumerate(secs):
                if x["name"] in self.taken.get(n, set()):
                    continue
                rank = (0, order.index(x["name"])) if x["name"] in order else (1, pi)
                cand.append((rank, vi, n, x))
        cand.sort(key=lambda t: (t[0], t[1]))
        fl = self.walk["fill"]
        j = 0
        for _r, _vi, n, x in cand:
            if not isinstance(x.get("bytes"), int) or x["bytes"] > self.left:
                fl["skipped"] += 1
                continue
            j += 1
            holder: dict[str, Any] = {}
            k = next((st["k"] for st in self.walk["steps"] if st.get("node") == n), 0)
            got = await self.expand(k, n, holder, self.peeks[n][0], [], [], [(x["name"], "fill")], stage=f"hub_fill#{j}")
            c = (holder.get("expand") or {}).get("calls", [{}])[-1]
            fl["sections"].append({"node": n, "section": x["name"], "bytes": c.get("bytes"),
                                   **({"excluded": True} if c.get("excluded") else {})})
            if not c.get("excluded"):
                fl["bytes"] += c.get("bytes") or 0
                fl["fact_ids"] += [f["id"] for f in got]

    def _note_required(self, node: str, holder: dict, got: list[dict]) -> None:
        req = self.walk["required"]
        calls = [c for c in (holder.get("expand") or {}).get("calls") or [] if c.get("why") == "required"]
        for c in calls:
            if not any(c is x for x in req["calls_ref"]):
                req["calls_ref"].append(c)
                req["calls"].append({"node": node, **{x: c.get(x) for x in ("sections", "bytes", "excluded") if c.get(x) is not None}})
                if not c.get("excluded"):
                    req["bytes"] += c["bytes"]
        req["fact_ids"] += [f["id"] for f in got if f.get("required")]

    async def required_end(self) -> None:
        """걷기 고침 2 ① — 걸음 뒤 답에 나올 시술(모은 줄의 출처 중 단위 · 제품 · 계열 · rank 순 위 WALK_REQUIRED_MAX)의
        checks · site_caution 을 Jev 와 무관하게 받음(남은 예산 안 · 넘으면 미리 뺌)."""
        req = self.walk["required"]
        order = list(dict.fromkeys(f["node"] for f in self.facts if self.kind(f["node"]) in PROC_NODE_KINDS))
        for i, n in enumerate(order[: self.s.walk_required_max], start=1):
            if n not in req["nodes"]:
                req["nodes"].append(n)
            if n in self.required_done:
                continue
            self.required_done.add(n)
            items = self.safety_items(self.walk.get("intent"))
            plan, filt = self.plan_extra(n, self.peeks[n][0], items)
            req["filtered"] += [f"{n}|{x}" for x in filt]
            self._note_cut(n, [(x, w) for x, w in items if x in filt])
            if not plan:
                continue
            k = next((st["k"] for st in self.walk["steps"] if st.get("node") == n), 0)
            holder: dict[str, Any] = {}
            got = await self.expand(k, n, holder, self.peeks[n][0], [], [], plan, stage=f"hub_req#{i}")
            self._note_required(n, holder, got)

    async def safety_checks(self) -> None:
        """고침 4 A(코디 11-01) — 안전 예산 안: 답에 나올 시술(필수 묶음을 받은 시술 · rank 순)의 requires_check ·
        requires_check_unit 이웃 확인 조건 노드 자신의 checks 줄(K8 — 시술 checks section 엔 없음). 이웃 목록(앞 4 밖은
        expand(neighbor_kinds) · 예산 밖) → 질문이 말한 확인 조건(① resolve 후보) 먼저 · 그다음 시술 rank → 허브 순(금기류 · 등급) ·
        peek(예산 밖) + expand(checks · 1쪽) · 넘으면 그 노드만 자르고 다음. 기록 walk.safety_budget.checks."""
        sb = self.walk["safety_budget"]
        info = sb.setdefault("checks", {"conditions": [], "list_calls": 0, "taken": [], "cut": []})
        conds: list[str] = []
        for n in self.walk["required"]["nodes"]:
            pk = (self.peeks.get(n) or ({},))[0]
            for kd in CHECK_EDGE_KINDS:
                grp = (pk.get("neighbors_by_kind") or {}).get(kd) or {}
                ids = [it.get("id") for it in grp.get("items") or [] if it.get("id")]
                if (grp.get("n") or 0) > len(ids):
                    try:
                        ex, _eb, _ms = await _hub(self.deps, self.timer, f"hub_list#chk-{info['list_calls'] + 1}", self.walk,
                                                  lambda n_=n, k_=kd: self.deps.hub_rest.expand(n_, neighbor_kinds=[k_]))
                        info["list_calls"] += 1
                        ids = [r.get("id") for r in ex.get("neighbors") or [] if isinstance(r, dict) and r.get("id")] + \
                            list(ex.get("neighbors_more_ids") or [])
                    except Exception as e:  # noqa: BLE001 — 목록 실패면 앞 4 로(기록)
                        info.setdefault("errors", []).append(f"list {n} {kd}: {type(e).__name__}"[:200])
                for c in ids:
                    if c not in conds:
                        conds.append(c)
                        self.edge_of.setdefault(c, kd)
        said = set((self.walk.get("resolve") or {}).get("candidates") or [])
        conds = [c for c in conds if c in said] + [c for c in conds if c not in said]
        info["conditions"] = conds
        j = 0
        for c in conds:
            if self.left_of("safety") <= 0:
                info["cut"] += [x for x in conds[conds.index(c):] if x not in info["taken"]]
                break
            try:
                if c not in self.peeks:
                    pk, pb, pms = await _hub(self.deps, self.timer, f"hub_peek#chk-{c.split(':')[-1]}", self.walk,
                                             lambda c_=c: self.deps.hub_rest.peek(c_))
                    self.peeks[c] = (pk, pb, pms)
                pk = self.peeks[c][0]
            except Exception as e:  # noqa: BLE001 — 그 확인 조건만 건너뜀(기록)
                info.setdefault("errors", []).append(f"peek {c}: {type(e).__name__}"[:200])
                continue
            plan, filt = self.plan_extra(c, pk, [("checks", "safety")])
            if filt:
                info["cut"].append(c)
                self._note_cut(c, [("checks", "safety")])
                continue
            if not plan:
                continue
            j += 1
            holder: dict[str, Any] = {}
            got = await self.expand(0, c, holder, pk, [], [], plan, stage=f"hub_safe#{j}")
            if got:
                info["taken"].append(c)
            self._note_required(c, holder, got)

    @property
    def left(self) -> int:
        return self.s.walk_byte_budget - self.walk["bytes_total"]

    async def peek(self, k: int, node: str, step: dict) -> dict:
        if node in self.peeks:
            step.update({"peek_bytes": None, "peek_ms": None, "peek_reused": True})
            return self.peeks[node][0]
        peek, pb, pms = await _hub(self.deps, self.timer, f"hub_peek#{k}", self.walk,
                                   lambda: self.deps.hub_rest.peek(node))  # 걸음 경로 = intent 안 넘김(F-1)
        self.peeks[node] = (peek, pb, pms)
        step.update({"peek_bytes": pb, "peek_ms": pms, "peek_reused": False})
        if peek.get("safety") and node not in self.safety:
            self.safety[node] = peek["safety"]
        if node == self.start:
            self.rule = (peek.get("claude_contract") or {}).get("rule")
        return peek

    def plan_extra(self, node: str, peek: dict, items: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[str]]:
        """필수 묶음 · 고정 섹션 [(section, why)] — peek 에 있고 아직 안 받은 섹션만 · 앞에서부터 예산 차감 ·
        peek bytes 가 남은 예산을 넘으면 미리 뺌(부르지 않음)."""
        have = {x.get("name"): x.get("bytes") for x in peek.get("sections") or [] if x.get("n_facts", 1)}
        taken = self.taken.get(node, set())
        out, filt = [], []
        left = {"general": self.left, "safety": self.left_of("safety")}
        for x, why in items:
            if x not in have or x in taken or any(x == y for y, _ in out):
                continue
            b = have[x] if isinstance(have[x], int) else 0
            pl = self.pool(why)
            if b > left[pl]:
                filt.append(x)
                continue
            left[pl] -= b
            out.append((x, why))
        return out, filt

    async def expand(self, k: int, node: str, step: dict, peek: dict, secs: list[str], nbs: list[str],
                     extra: list[tuple[str, str]] = (), stage: str | None = None) -> list[dict]:
        """이웃 slim 먼저(한 번) → 필수 묶음 · 고정 섹션(extra · 예산 먼저 차감) → Jev 섹션 하나씩. 남은 예산을 넘는 호출은
        그것만 빼고(excluded) 다음으로. → 받은 줄. 같은 걸음에 두 번 부르면 calls 를 이어 붙임."""
        ex_secs = [x for x, _ in extra]
        plan = ([("neighbors", nbs, "jev")] if nbs else []) + [("sections", [x], w) for x, w in extra] + \
            [("sections", [x], "jev") for x in secs if x not in ex_secs and x not in self.taken.get(node, set())]
        if not plan:
            return []
        pb_of = {x.get("name"): x.get("bytes") for x in peek.get("sections") or []}
        prev = step.get("expand") or {}
        calls: list[dict] = list(prev.get("calls") or [])
        got: list[dict] = []
        j0 = len(calls)
        for j, (what, arg, why) in enumerate(plan, start=j0):
            inp = {what: arg}
            name = (stage or f"hub_expand#{k}") + (f"-{j + 1}" if j else "")
            ex, eb, ems = await _hub(self.deps, self.timer, name, self.walk,
                                     lambda a=inp: self.deps.hub_rest.expand(node, **a))
            call: dict[str, Any] = {"sections": arg if what == "sections" else [], "neighbors": arg if what == "neighbors" else [],
                                    "input": inp, "bytes": eb, "ms": ems}
            if why != "jev":
                call["why"] = why
            if what == "sections":
                self.taken.setdefault(node, set()).add(arg[0])
                pb = pb_of.get(arg[0])
                if isinstance(pb, int) and pb != eb:  # peek bytes ≠ 실제(차 0 이어야 함 · 실제 바이트로 판단)
                    call["peek_bytes"] = pb
                    self.walk["bytes_mismatch"] += 1
            calls.append(call)
            pl = self.pool(why)
            if pl == "safety":
                call["pool"] = "safety"
            if eb > self.left_of(pl):  # 그 호출만 통째로 뺌(잘라 넣지 않음) · 다음 섹션은 계속
                call["excluded"] = True
                if pl == "safety":
                    self._note_cut(node, [(arg[0], why)], eb)
                else:
                    self.walk["budget_hit"] = True
                continue
            if pl == "safety":  # 고침 4 — 안전 예산(20KB 밖)
                sb = self.walk["safety_budget"]
                sb["bytes"] += eb
                sb["sections"].append({"node": node, "section": arg[0], "bytes": eb})
            else:
                self.walk["bytes_total"] += eb
            rows = [f for f in ex.get("facts") or [] if isinstance(f, dict) and f.get("id")]
            call["fact_ids"] = [f["id"] for f in rows]
            for f in rows:
                if f["id"] in self.fact_seen:
                    continue
                self.fact_seen.add(f["id"])
                line = f.get("line", f.get("text"))
                row = {"id": f["id"], "line": line, "section": f.get("section"), "node": node, "step": k}
                if why in ("required", "safety"):
                    row["required"] = True
                if f.get("cut"):
                    row["cut"] = True
                if f.get("flags"):
                    row["flags"] = f["flags"]
                self.facts.append(row)
                got.append(row)
            for sec, n in (ex.get("more") or {}).items():
                self.walk["more"][sec] = self.walk["more"].get(sec, 0) + n
            self.flags |= set((ex.get("claude_contract") or {}).get("must_show_flags") or [])
            for n in ex.get("neighbors") or []:
                if not isinstance(n, dict) or not n.get("id"):
                    continue
                self.cards.setdefault(n["id"], {x: n.get(x) for x in ("id", "kind", "name", "edge", "kind_edge", "value",
                                                                       "sections_n") if n.get(x) is not None})
                if n.get("safety") and n["id"] not in self.safety:
                    self.safety[n["id"]] = n["safety"]
            if what == "neighbors":
                for x in arg:
                    if x not in self.seen:
                        self.seen.add(x)
                        self.queue.append({"node": x, "edge": self.edge_of.get(x), "hop": step.get("hop", 0) + 1})
            if what == "sections" and node == self.start and (peek.get("node") or {}).get("kind") in ("family", "product"):
                for u in ((ex.get("by_unit") or {}).get(arg[0]) or {}):  # v2.4 — 단위를 그 section 으로 대기열 뒤에
                    if (u, arg[0]) not in self.unit_seen:
                        self.unit_seen.add((u, arg[0]))
                        self.queue.append({"node": u, "section": arg[0], "edge": self.s_link, "hop": 1})
                        self.walk["by_unit_queued"].append({"unit": u, "section": arg[0]})
                        self.edge_of.setdefault(u, self.s_link)
        kept = [c for c in calls if not c.get("excluded")]
        step["expand"] = {"sections": [x for c in kept for x in c["sections"]],
                          "neighbors": [x for c in kept for x in c["neighbors"]],
                          "bytes": sum(c["bytes"] for c in kept), "ms": round(sum(c["ms"] for c in calls), 1),
                          "fact_ids": list(prev.get("fact_ids") or []) + [f["id"] for f in got], "calls": calls}
        excl = [x for c in calls if c.get("excluded") for x in c["sections"] + c["neighbors"]]
        if excl:
            step["expand"]["excluded"] = excl
        return got

    async def full(self) -> dict:
        """⑤ cut 줄 원문 — rank 순 · 출처 노드마다 expand(node, fact_ids) 1회 · WALK_FULL_BYTE_BUDGET · WALK_FULL_FACTS_MAX 안.
        응답 줄을 순서대로 담아 예산을 넘는 줄부터는 안 담음(인용 불가 줄로)."""
        s = self.s
        out = {"calls": 0, "fact_ids": [], "cut_unfetched_ids": [], "bytes": 0, "bytes_used": 0, "over_budget": False,
               "calls_detail": []}
        cut = [f for f in self.facts if f.get("cut")]
        want: dict[str, list[str]] = {}
        n_left = s.walk_full_facts_max
        for f in cut:
            if n_left <= 0:
                break
            want.setdefault(f["node"], []).append(f["id"])
            n_left -= 1
        texts: dict[str, str] = {}
        for j, (node, ids) in enumerate(want.items(), start=1):
            if out["bytes_used"] >= s.walk_full_byte_budget:
                break
            try:
                ex, eb, ems = await _hub(self.deps, self.timer, f"hub_full#{j}", self.walk,
                                         lambda n=node, x=ids: self.deps.hub_rest.expand(n, fact_ids=x))
            except Exception as e:  # noqa: BLE001 — 원문 못 받음 = 그 줄은 인용 불가로
                out["calls_detail"].append({"node": node, "fact_ids": ids, "error": f"{type(e).__name__}: {e}"[:200]})
                continue
            out["calls"] += 1
            out["bytes"] += eb
            rows = [r for r in ex.get("facts") or [] if isinstance(r, dict) and r.get("id") in ids and "text" in r]
            used = eb - sum(_compact(r) + 1 for r in rows)  # 봉투
            got = []
            for r in rows:
                rb = _compact(r) + 1
                if out["bytes_used"] + used + rb > s.walk_full_byte_budget:
                    out["over_budget"] = True
                    break
                used += rb
                texts[r["id"]] = r["text"]
                got.append(r["id"])
            out["bytes_used"] += max(used, 0)
            out["calls_detail"].append({"node": node, "input": {"fact_ids": ids}, "bytes": eb, "ms": ems, "fetched": got})
        out["fact_ids"] = [f["id"] for f in cut if f["id"] in texts]
        out["cut_unfetched_ids"] = [f["id"] for f in cut if f["id"] not in texts]
        self.texts = texts
        return out

    def payload(self, intent_row: dict) -> dict:
        """§3-4-7 Claude 꼴 — facts rank 순 · 출처 노드 · 인용 가능 id · 규칙 문장(판에 한 번)."""
        facts, allowed = [], []
        texts = self.texts
        for rank, f in enumerate(self.facts, start=1):
            n = f["node"]
            src = {"id": n}
            card = self.cards.get(n) or {}
            pk = (self.peeks.get(n) or ({},))[0].get("node") or {}
            src["kind"] = pk.get("kind") or card.get("kind")
            src["name"] = pk.get("name") or card.get("name")
            if n != self.start and self.edge_of.get(n):
                src["edge"] = self.edge_of[n]
            row: dict[str, Any] = {"id": f["id"]}
            if not f.get("cut"):
                row["text"] = f["line"]
                allowed.append(f["id"])
            elif f["id"] in texts:
                row["text"] = texts[f["id"]]
                allowed.append(f["id"])
            else:
                row.update({"line": f["line"]})
            row.update({"section": f["section"], "source": src, "step": f["step"], "rank": rank})
            if f.get("cut") and f["id"] not in texts:
                row.update({"cut": True, "quotable": False})
            if f.get("flags"):
                row["flags"] = f["flags"]
            facts.append(row)
        return {"intent": {"choice": intent_row.get("choice"), "confidence": intent_row.get("confidence")},
                "facts": facts, "neighbors": list(self.cards.values()), "safety": self.safety,
                "claude_contract": {"rule": self.rule, "allowed_fact_ids": allowed, "must_show_flags": sorted(self.flags)}}


async def run(deps: Deps, question: str, timer: CaseTimer, on_text: Callable[[str], None], seed: int = 0,
              provider: str = "sdk") -> dict:
    """case3 = Jev 재귀 탐색(§3-4-1 · v2.4). WALK_MAX_STEPS=0 이면 v1 경로만(fallback_run)."""
    s = deps.settings
    if s.walk_max_steps <= 0:
        return await fallback_run(deps, question, timer, on_text, seed, provider)
    res = new_case_result()
    cli_res: dict | None = None
    if deps.jev is None or (provider == "sdk" and deps.anthropic is None):
        res["error"] = NO_KEY
        return res
    trace: dict[str, Any] = {"hub_list": "cache" if deps.vocab is not None else "load", "_provider": provider}
    res["trace"] = trace
    tokens: dict[str, dict] = {}
    res["tokens"] = tokens
    table, table_sha = order_table(s)
    walk: dict[str, Any] = {"start_node": None, "intent": None, "order_kinds": [], "order_table_sha": table_sha,
                            "list": {"kinds": [], "calls": 0, "bytes": 0, "ids_n": 0, "ms": 0.0}, "by_unit_queued": [],
                            "step1_options": [], "bytes_mismatch": 0, "steps": [], "queue_left": [], "stop": None,
                            "steps_n": 0, "bytes_total": 0, "budget_hit": False, "neighbor_reached": 0,
                            "full": None, "more": {}, "fallback": False, "fallback_reason": None,
                            "required": {"nodes": [], "calls": [], "calls_ref": [], "bytes": 0, "fact_ids": [], "filtered": []},
                            "fixed": None, "enough_ignored": 0,
                            "safety_budget": {"budget": s.walk_safety_budget, "bytes": 0, "sections": [], "cut": []}, "base_bundle": None, "bundles": [],
                            "fill": {"sections": [], "bytes": 0, "fact_ids": [], "skipped": 0},
                            "jev_calls": 0, "jev_ms_total": 0.0, "hub_calls": 0, "hub_ms_total": 0.0, "fact_ids": []}
    trace["walk"] = walk
    try:
        if deps.vocab is None:
            deps.vocab = await deps.hub_rest.vocab()
        with timer.stage("hub_resolve"):
            resolved = await deps.hub_rest.resolve(question)
        cands, menus = resolve_candidates(resolved)
        trace["gold_build_hash"] = (resolved.get("build") or {}).get("build_hash")
        walk["resolve"] = {"candidates": [c["id"] for c in cands], "menu_candidates": [m["id"] for m in menus],
                           "unmatched": resolved.get("unmatched", [])}

        # ② jev#0 — 의도 Choice 12 + 시작 노드 Choice
        rng = random.Random(seed)
        v = deps.vocab.get("vocab", deps.vocab)
        intents = {i["id"]: f"{i['name']} · 예: {' / '.join(i.get('examples') or INTENT_EXAMPLES.get(i['id'], []))}"
                   for i in v.get("intent", [])}
        start_crit, start_src = walk_start_options(deps.vocab, [c for c in cands if c.get("kind") != "menu"])
        qs: dict[str, Any] = {"intent": Choice(instructions="이 상담 질문의 의도는 무엇인가?", criteria=_shuffled(rng, intents))}
        if start_crit:
            qs["start"] = Choice(instructions="이 질문에 답하려면 어느 노드에서 찾기 시작해야 하나?",
                                 criteria=_shuffled(rng, {**start_crit, NONE: "질문이 이 중 어느 것도 말하지 않음"}))
        try:
            resp, ms = await _jev(deps, timer, "jev#0", question, qs, tokens, walk)
        except TypeSafeError as e:
            res["error"] = _jev_error(e)
            return res
        intent_row = _choice_row(resp.choices["intent"])
        walk["intent"] = intent_row["choice"]
        walk["jev0"] = {"model": resp.model, "ms": ms, "usage": {"input_tokens": resp.usage.input_tokens},
                        "intent": {**intent_row, "low_confidence": intent_row["confidence"] < s.jev_confidence_min},
                        "start_source": start_src, "start_options_n": len(start_crit), "option_order_seed": seed}
        if "start" in resp.choices:
            sr = _choice_row(resp.choices["start"])
            walk["jev0"]["start"] = {**sr, "low_confidence": sr["confidence"] < s.jev_confidence_min}
            if sr["choice"] != NONE:
                walk["start_node"] = sr["choice"]
        intent = walk["intent"]
        S = walk["start_node"]
        reason = None if S else "no_start"
        if intent in NO_WALK_INTENTS:
            reason = "no_walk_intent"
        wk = Walker(deps, timer, walk, S) if not reason else None

        # ③ 걸음 1 — 이웃 먼저(§3-4-6 표) + 섹션(의도별 상한) 한 요청 · 「충분」 없음
        if wk is not None:
            step: dict[str, Any] = {"k": 1, "node": S, "phase": "first", "hop": 0}
            walk["steps"].append(step)
            try:
                peek = await wk.peek(1, S, step)
            except Exception as e:  # noqa: BLE001 — 첫 peek 실패 = 되돌아감
                step["error"] = f"{type(e).__name__}: {e}"[:300]
                reason = "peek_failed"
        if wk is not None and not reason:
            s_kind = (peek.get("node") or {}).get("kind")
            wk.s_link = (table.get("unit_link") or {}).get(s_kind or "")
            walk["order_kinds"] = order_kinds(table, intent, s_kind)
            await wk.extras(1, S, step, peek, intent)  # 필수 묶음 · 고정 섹션(예산 먼저)
            nb_opts, kind_of = await step1_neighbors(deps, timer, walk, S, peek, walk["order_kinds"])
            sec_opts, filt = section_options(peek, wk.taken.get(S, set()), wk.left)
            if not s.walk_jev_sections:  # 고침 3 ② — Jev 는 이웃만(섹션 양은 규칙)
                sec_opts, filt = {}, []
            walk["step1_options"] = list(nb_opts) + list(sec_opts)
            if nb_opts or sec_opts:
                try:
                    jrow = await walk_pick_first(deps, timer, question, intent, peek, nb_opts, kind_of, sec_opts, seed,
                                                 tokens, walk)
                except TypeSafeError as e:
                    step["error"] = _jev_error(e)
                    walk["stop"] = "error"
                    jrow = None
                if jrow is not None:
                    jrow["filtered_by_budget"] = filt
                    step["jev"] = jrow
                    for x in jrow["picked_neighbors"]:
                        wk.edge_of[x] = kind_of.get(x)
                    await wk.expand(1, S, step, peek, jrow["picked_sections"], jrow["picked_neighbors"])
            else:
                step["jev"] = None
                walk["stop"] = "empty"

            # ④ 걸음 2..N — 대기열 앞 · 비면 S 로 돌아옴(peek 재사용) · 섹션 ≤ 2 · 이웃은 대기열 뒤
            returned = False
            k = 1
            while walk["stop"] is None:
                if k >= s.walk_max_steps:
                    walk["stop"] = "steps"
                    break
                if wk.queue:
                    entry, phase = wk.queue.pop(0), "queue"
                elif not returned:
                    entry, phase, returned = {"node": S}, "return", True
                else:
                    walk["stop"] = "empty"
                    break
                k += 1
                node = entry["node"]
                step = {"k": k, "node": node, "phase": phase, "hop": entry.get("hop", 0)}
                if entry.get("section"):
                    step["by_unit_section"] = entry["section"]
                walk["steps"].append(step)
                try:
                    peek = await wk.peek(k, node, step)
                except Exception as e:  # noqa: BLE001 — 뒤 걸음 peek 실패는 그 걸음만
                    step["error"] = f"{type(e).__name__}: {e}"[:300]
                    continue
                n_before = len(wk.facts)
                await wk.extras(k, node, step, peek, intent)  # 첫 시술 노드면 고정 섹션
                taken = wk.taken.get(node, set())
                if entry.get("section"):  # by_unit — Jev 섹션 고름 없이 그 section 만(예산 안)
                    opts, filt = section_options(peek, taken, wk.left)
                    step["jev"] = None
                    if SEC + entry["section"] in opts:
                        got = await wk.expand(k, node, step, peek, [entry["section"]], [])
                    else:
                        got = []
                        step["filtered_by_budget"] = [x for x in filt if x == entry["section"]]
                        if step["filtered_by_budget"]:
                            walk["budget_hit"] = True
                    if (got or len(wk.facts) > n_before) and node != S:
                        walk["neighbor_reached"] += 1
                    continue
                if len(wk.facts) > n_before and node != S:  # 고정 섹션만으로도 이웃 fact 도달
                    walk["neighbor_reached"] += 1
                    n_before = -1
                opts, kind_of, filt = walk_options(peek, wk.seen, taken, wk.left)
                if not s.walk_jev_sections:  # 고침 3 ② — 이웃만
                    opts = {x: d for x, d in opts.items() if kind_of.get(x) != "section"}
                    kind_of = {x: kd for x, kd in kind_of.items() if x in opts}
                    filt = []
                if not opts:
                    step["jev"] = {"filtered_by_budget": filt, "options_n": 0} if filt else None
                    if filt:  # 남은 예산 < 가장 작은 섹션 · 이웃 선택지 없음
                        walk["budget_hit"] = True
                        walk["stop"] = "budget"
                    continue
                try:
                    jrow = await walk_pick(deps, timer, k, question, peek, opts, kind_of, wk.facts, seed, tokens, walk)
                except TypeSafeError as e:
                    step["error"] = _jev_error(e)
                    walk["stop"] = "error"
                    break
                jrow["filtered_by_budget"] = filt
                step["jev"] = jrow
                said = (jrow.get("enough") or 0) >= s.walk_enough_min or (not jrow["picked"] and (
                    jrow.get("choice") == NONE_ENOUGH or (jrow.get("kind") or {}).get("choice") == NONE_ENOUGH))
                if wk.facts and said:
                    if walk["bytes_total"] < s.walk_enough_bytes_ratio * s.walk_byte_budget:  # 고침 2 ② — 덜 모았으면 무시
                        walk["enough_ignored"] += 1
                        jrow["enough_ignored"] = True
                    else:
                        walk["stop"] = "enough"
                        break
                if not jrow["picked"]:
                    continue
                for x in jrow["picked_neighbors"]:
                    wk.edge_of.setdefault(x, kind_of.get(x))
                got = await wk.expand(k, node, step, peek, jrow["picked_sections"], jrow["picked_neighbors"])
                if got and node != S and n_before >= 0:
                    walk["neighbor_reached"] += 1
            walk["queue_left"] = [e["node"] for e in wk.queue]
        walk["steps_n"] = len(walk["steps"])
        walk["fact_ids"] = [f["id"] for f in wk.facts] if wk else []
        if not reason and not walk["fact_ids"]:
            reason = "no_facts"
        if reason:  # 되돌아가기 — v1 경로 1회(의도 기본 묶음 · §3-4-3)
            walk.update({"fallback": True, "fallback_reason": reason})
            user, err = await fallback_path(deps, question, timer, seed, trace, tokens, resolved=resolved)
            if err:
                res["error"] = err
                return res
        else:
            await wk.required_end()  # 고침 2 ① 필수 묶음(답에 나올 시술 위 3)
            if s.walk_safety_budget > 0 and s.walk_safety_checks:
                await wk.safety_checks()  # 고침 4 A — 확인 조건 이웃 checks 줄(안전 예산)
            if s.walk_fill:
                await wk.fill(intent)  # 고침 3 ③ 남은 예산 채우기
            walk["required"].pop("calls_ref", None)
            walk["fact_ids"] = [f["id"] for f in wk.facts]
            walk["full"] = await wk.full()  # ⑤ cut 줄 원문
            payload = wk.payload(walk["jev0"]["intent"])
            text, cut = _cap(deps, json.dumps(payload, ensure_ascii=False), provider)
            if cut:
                walk["truncated_to"] = _cap_of(deps, provider)
            user = f"질문: {question}\n\n[허브 결과 — Jev 걸음으로 모은 것]\n{text}"
        cli_res = await _answer(deps, user, timer, on_text, provider, res, tokens, trace)
    except Exception as e:  # noqa: BLE001 — 허브 · Sonnet 오류 — 그 열만 오류
        res["error"] = f"{type(e).__name__}: {e}"[:500]
    finally:
        walk["steps_n"] = len(walk["steps"])
        (walk.get("required") or {}).pop("calls_ref", None)
        _finish(res, timer, cli_res)
    return res
