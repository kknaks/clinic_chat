"""case3 — 앱 서버: 허브 사전(T1) → Jev(의도 · 슬롯 Choice) → 골드 도구 경로 1번 → Sonnet 1회(SPEC-006 §3-4).

① hub_resolve: T1 resolve(질문 전체 · 질문마다)
② jev: 의도 Choice 12 + 슬롯 Choice(vocab 종류 · 255 넘는 시술 · 제품은 ① 후보로 줄임) + 「해당 없음」 짝 Noul
       + 여러 고민 Noul(R-6) · 선택지 순서 seed 섞기(R-7) · confidence · p_max · 1·2위 비 · low_confidence
③ hub_path: 의도 → 도구(SPEC-005 §6-1) · 필수 슬롯 없으면 부르지 않고 「되묻기 필요」
④ claude: Sonnet 1회 · 도구 없음 · 입력 = 질문 + ③ 결과 + 슬롯 표
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
    out["sections"] = [sec for sec in INFO_SECTIONS if out["info"].get(sec, 0) >= NOUL_MIN]
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


def plan_path(intent: str, jev: dict, cands: list[dict], menus: list[dict], question: str,
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


async def run(deps: Deps, question: str, timer: CaseTimer, on_text: Callable[[str], None], seed: int = 0,
              provider: str = "sdk") -> dict:
    res = new_case_result()
    cli_res: dict | None = None
    if deps.jev is None or (provider == "sdk" and deps.anthropic is None):
        res["error"] = NO_KEY
        return res
    trace: dict[str, Any] = {"hub_list": "cache" if deps.vocab is not None else "load"}
    res["trace"] = trace
    tokens: dict[str, dict] = {}
    res["tokens"] = tokens
    try:
        if deps.vocab is None:
            deps.vocab = await deps.hub_rest.vocab()
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
            res["error"] = _jev_error(e)
            return res
        trace["jev_calls"] = 1
        tokens["jev"] = st["tokens"]
        jev = read_jev(resp, meta, deps.settings.jev_confidence_min)
        jev["option_order_seed"] = seed
        trace["jev"] = jev

        intent = (jev.get("intent") or {}).get("choice")
        procs = None
        if intent in TWO_PROC:
            procs = await two_procs(deps, question, intent, jev, cands, seed, timer, trace, tokens)
        path = plan_path(intent, jev, cands, menus, question, procs)
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
        hub_text, cut = ("(허브 호출 없음)", False) if hub_result is None else \
            cap_text(json.dumps(hub_result, ensure_ascii=False), deps.settings.tool_result_max_bytes)
        if cut:  # case1 · 2 도구 결과와 같은 상한(세 케이스 같은 조건)
            trace["hub"]["truncated_to"] = deps.settings.tool_result_max_bytes
        user = (f"질문: {question}\n\n[앱이 해석한 의도 · 슬롯 표]\n{json.dumps(payload, ensure_ascii=False)}"
                f"\n\n[허브 결과]\n{hub_text}")
        if provider in CLI_PROVIDERS:  # ④ 를 CLI 로(도구 없음 · MCP 안 붙임) — 앞 단계는 그대로 앱
            cli_res = await cli.run(answer_spec(deps.settings, provider), system=prompts.system_for("case3"),
                                    prompt=user, timer=timer, on_text=on_text)
            for k in ("answer", "stop_reason", "refusal", "error", "structured"):
                res[k] = cli_res.get(k)
            tokens["llm"] = cli_res["tokens"]
            trace["cli"] = cli_res["trace"]["cli"]
            return res
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
    except Exception as e:  # noqa: BLE001 — 허브 · Sonnet 오류 — 그 열만 오류
        res["error"] = f"{type(e).__name__}: {e}"[:500]
    finally:
        if cli_res is not None:  # CLI 가 timer 를 끝냄 — cli_boot · ttft_kind 칸 그대로
            res["timing"] = cli_res["timing"]
            cost = case_cost(timer.stages)  # Jev(API)
            est = cli_res["cost"].get("usd_estimate")
            cost.update({"billed": "subscription", "usd_estimate": est,
                         "usd": round(cost["usd"] + (est or 0.0), 6)})
            res["cost"] = cost
        else:
            timer.finish()
            res["timing"] = timer.as_dict()
            res["cost"] = case_cost(timer.stages)
    return res
