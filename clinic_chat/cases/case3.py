"""case3 — 앱 서버: 허브 사전(T1) → Jev 재귀 탐색(peek → Choice → expand) → Sonnet 1회(SPEC-006 v2 §3-4).

① hub_resolve: T1 resolve(질문 전체 · 질문마다)
② jev#0: 의도 Choice 12 + 시작 노드 Choice(① 후보 · 비면 vocab 계열 · 고민 · 부위 · 「해당 없음」)
③ 걸음 k = 1..WALK_MAX_STEPS: hub_peek#k → jev#k(Choice「펼칠 것」 + 짝 Noul + Noul「충분」) → hub_expand#k
   멈춤 = enough · steps · budget(WALK_BYTE_BUDGET 넘는 expand 는 통째로 뺌) · empty
④ claude: Sonnet 1회 · 도구 없음 · 입력 = 질문 + 걸음마다 모은 fact + 안전 요약 + claude_contract
되돌아가기(시작 노드 없음 · 첫 peek 실패 · 모은 fact 0) = v1 경로 1회(`fallback_*` — 의도 → 골드 도구 경로 · 의도 묶음).
WALK_MAX_STEPS=0 이면 v1 경로만(되돌림 스위치).
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable
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
    "safety.pih": "색소 침착(PIH) 위험", "checks": "해도 되는지 확인할 조건(임신 · 질환 · 복용약 등)",
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


# ───────────── Jev 걸음(SPEC-006 v2 §3-4 · 보충 jev-walk) ─────────────
NONE_ENOUGH = "없음(충분)"
WALK_NEIGHBORS_MAX = 3  # 걸음당 다음 peek 이웃 수(W-3)
START_KINDS = ("family", "concern", "site")  # ① 후보가 비면 시작 노드를 고르는 vocab 종류(Q-4)
SEC = "section:"


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


def walk_options(peek: dict, seen: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    """peek → 펼칠 것 선택지(섹션 이름 ∪ 이웃 id) · 이웃 → edge kind. 이미 본 노드는 뺌."""
    opts: dict[str, str] = {}
    kind_of: dict[str, str] = {}
    for sec in peek.get("sections") or []:
        nm = sec.get("name")
        if nm and sec.get("n_facts", 1):
            opts[SEC + nm] = f"{nm}: {INFO_SECTIONS.get(nm, nm)} (fact {sec.get('n_facts')} · {sec.get('bytes')}B)"
            kind_of[SEC + nm] = "section"
    for edge, grp in (peek.get("neighbors_by_kind") or {}).items():
        for it in grp.get("items") or []:
            i = it.get("id")
            if i and i not in seen and i not in opts:
                opts[i] = f"{it.get('name') or i} — {edge} · {it.get('label') or ''}".rstrip(" ·")
                kind_of[i] = edge
    return opts, kind_of


def _state(question: str, node: dict, steps_out: list[dict]) -> str:
    """Jev state — 질문 · 지금 노드 · 지금까지 모은 fact 원문."""
    got = [f"- [{f.get('section')}] {f.get('text')}" for s in steps_out for f in s["facts"]]
    return (f"질문: {question}\n\n지금 노드: {node.get('name')}({node.get('kind')})\n\n지금까지 모은 정보:\n"
            + ("\n".join(got) if got else "(없음)"))


def _choice_row(a: Any) -> dict:
    p_max, ratio = choice_stats(dict(a.probabilities))
    return {"choice": a.choice, "confidence": round(a.confidence, 4), "p_max": round(p_max, 4), "ratio": ratio}


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
    b = len(json.dumps(d, ensure_ascii=False).encode("utf-8"))
    walk["hub_calls"] += 1
    walk["hub_ms_total"] = round(walk["hub_ms_total"] + ms, 1)
    return d, b, ms


async def walk_pick(deps: Deps, timer: CaseTimer, k: int, question: str, peek: dict, opts: dict[str, str],
                    kind_of: dict[str, str], steps_out: list[dict], seed: int, tokens: dict, walk: dict) -> dict:
    """jev#k — Choice「펼칠 것」(+ 「없음(충분)」) · 선택지마다 짝 Noul · Noul「충분」. 255 넘으면 kind 를 먼저(2단)."""
    rng = random.Random(seed * 1000 + k)
    state = _state(question, peek.get("node") or {}, steps_out)
    enough_q = Noul(instructions="지금까지 모은 정보로 이 질문에 답하기에 충분한가?")
    two = len(opts) + 1 > CHOICE_MAX
    row: dict[str, Any] = {"options_n": len(opts), "two_stage": two}
    ms_total = 0.0
    usage = 0
    if two:  # 1단: 종류(섹션 · edge kind) Choice + 충분
        kinds = sorted(set(kind_of.values()))
        crit = {kd: ("섹션(이 노드의 원문 묶음)" if kd == "section" else f"이웃 — {kd}") for kd in kinds}
        crit[NONE_ENOUGH] = "더 펼칠 것 없음 — 지금까지로 충분"
        resp, ms = await _jev(deps, timer, f"jev#{k}", state, {"kind": Choice(
            instructions="이 질문에 답하려고 다음에 펼칠 종류는?", criteria=_shuffled(rng, crit)), "enough": enough_q},
            tokens, walk)
        ms_total += ms
        usage += resp.usage.input_tokens
        a = resp.choices["kind"]
        row["kind"] = _choice_row(a)
        row["enough"] = round(resp.nouls["enough"].noul, 4)
        if a.choice == NONE_ENOUGH or row["enough"] >= deps.settings.walk_enough_min:
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
    also = sorted(((resp.nouls[f"also:{n}"].noul, n) for n in range(len(keys))
                   if resp.nouls.get(f"also:{n}") and resp.nouls[f"also:{n}"].noul >= NOUL_MIN), key=lambda t: (-t[0], t[1]))
    picked = ([a.choice] if a.choice != NONE_ENOUGH else []) + [keys[n] for _, n in also]
    picked = list(dict.fromkeys(picked))
    nbs = [x for x in picked if not x.startswith(SEC)][:WALK_NEIGHBORS_MAX]
    row["picked"] = [x for x in picked if x.startswith(SEC)] + nbs
    row["ms"] = round(ms_total, 1)
    row["usage"] = {"input_tokens": usage}
    return row


async def run(deps: Deps, question: str, timer: CaseTimer, on_text: Callable[[str], None], seed: int = 0,
              provider: str = "sdk") -> dict:
    """case3 = Jev 재귀 탐색(§3-4-1). WALK_MAX_STEPS=0 이면 v1 경로만(fallback_run)."""
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
    walk: dict[str, Any] = {"start_node": None, "intent": None, "steps": [], "stop": None, "steps_n": 0,
                            "bytes_total": 0, "budget_hit": False, "fallback": False, "fallback_reason": None,
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

        # ③ 걸음
        steps_out: list[dict] = []  # Sonnet 입력(걸음 순서)
        safety: dict[str, Any] = {}  # 노드마다 한 번(예산 밖 · 늘 포함)
        contract = {"allowed_fact_ids": [], "must_show_flags": [], "priority": None, "rule": None}
        frontier = [walk["start_node"]] if walk["start_node"] else []
        seen: set[str] = set(frontier)
        reason = None if frontier else "no_start"
        for k in range(1, s.walk_max_steps + 1):
            if reason or not frontier:
                walk["stop"] = walk["stop"] or "empty"
                break
            node = frontier.pop(0)
            step: dict[str, Any] = {"k": k, "node": node}
            walk["steps"].append(step)
            try:
                peek, pb, pms = await _hub(deps, timer, f"hub_peek#{k}", walk,
                                           lambda n=node: deps.hub_rest.peek(n, intent=intent))
            except Exception as e:  # noqa: BLE001 — 첫 peek 실패 = 되돌아감 · 뒤 걸음이면 그 걸음만
                step["error"] = f"{type(e).__name__}: {e}"[:300]
                if k == 1:
                    reason = "peek_failed"
                    break
                continue
            step.update({"peek_bytes": pb, "peek_ms": pms})
            if peek.get("safety") and node not in safety:
                safety[node] = peek["safety"]
            opts, kind_of = walk_options(peek, seen)
            if not opts:
                step["jev"] = None
                if not frontier:
                    walk["stop"] = "empty"
                    break
                continue
            try:
                jrow = await walk_pick(deps, timer, k, question, peek, opts, kind_of, steps_out, seed, tokens, walk)
            except TypeSafeError as e:
                step["error"] = _jev_error(e)
                walk["stop"] = "error"
                break
            step["jev"] = jrow
            if jrow.get("enough", 0) >= s.walk_enough_min or (not jrow["picked"] and jrow.get("choice") == NONE_ENOUGH) \
                    or ("kind" in jrow and not jrow["picked"]):
                walk["stop"] = "enough"
                break
            secs = [x[len(SEC):] for x in jrow["picked"] if x.startswith(SEC)]
            nbs = [x for x in jrow["picked"] if not x.startswith(SEC)]
            ex, eb, ems = await _hub(deps, timer, f"hub_expand#{k}", walk, lambda n=node, a=secs, b=nbs: deps.hub_rest.expand(
                n, sections=a or None, neighbors=b or None, intent=intent))
            facts = [{"id": f.get("id"), "text": f.get("text"), "section": f.get("section")}
                     for f in ex.get("facts") or [] if isinstance(f, dict)]
            step["expand"] = {"sections": secs, "neighbors": nbs, "bytes": eb, "ms": ems,
                              "fact_ids": [f["id"] for f in facts]}
            if walk["bytes_total"] + eb > s.walk_byte_budget:  # 예산 넘으면 통째로 뺌(잘라 넣지 않음)
                step["expand"]["excluded"] = True
                walk["budget_hit"] = True
                walk["stop"] = "budget"
                break
            walk["bytes_total"] += eb
            nb_cards = [{x: n.get(x) for x in ("id", "kind", "name", "edge", "value", "sections_n") if n.get(x) is not None}
                        for n in ex.get("neighbors") or [] if isinstance(n, dict)]
            for n in ex.get("neighbors") or []:
                if isinstance(n, dict) and n.get("safety") and n.get("id") not in safety:
                    safety[n["id"]] = n["safety"]
            steps_out.append({"step": k, "node": node, "picked": jrow["picked"], "facts": facts,
                              **({"neighbors": nb_cards} if nb_cards else {})})
            cc = ex.get("claude_contract") or {}
            contract["must_show_flags"] = sorted(set(contract["must_show_flags"]) | set(cc.get("must_show_flags") or []))
            contract["priority"] = contract["priority"] or cc.get("priority")
            contract["rule"] = contract["rule"] or cc.get("rule")
            for x in nbs:
                if x not in seen:
                    seen.add(x)
                    frontier.append(x)
        else:
            walk["stop"] = walk["stop"] or "steps"
        walk["steps_n"] = len(walk["steps"])
        walk["fact_ids"] = [f["id"] for st in steps_out for f in st["facts"]]
        if not reason and not walk["fact_ids"]:
            reason = "no_facts"
        if reason:  # 되돌아가기 — v1 경로 1회(의도 기본 묶음 · §3-4-3)
            walk.update({"fallback": True, "fallback_reason": reason})
            user, err = await fallback_path(deps, question, timer, seed, trace, tokens, resolved=resolved)
            if err:
                res["error"] = err
                return res
        else:
            contract["allowed_fact_ids"] = list(dict.fromkeys(walk["fact_ids"]))
            payload = {"intent": walk["jev0"]["intent"], "steps": steps_out, "safety": safety,
                       "claude_contract": contract}
            text, cut = _cap(deps, json.dumps(payload, ensure_ascii=False), provider)
            if cut:
                walk["truncated_to"] = _cap_of(deps, provider)
            user = f"질문: {question}\n\n[허브 결과 — Jev 걸음으로 모은 것]\n{text}"
        cli_res = await _answer(deps, user, timer, on_text, provider, res, tokens, trace)
    except Exception as e:  # noqa: BLE001 — 허브 · Sonnet 오류 — 그 열만 오류
        res["error"] = f"{type(e).__name__}: {e}"[:500]
    finally:
        walk["steps_n"] = len(walk["steps"])
        _finish(res, timer, cli_res)
    return res
