"""① 단어 검사(SPEC-006 §5-1) — 이름 뽑기(LLM · R-4) → 정규화 → 원장 21 부분 일치 → 시맨틱 사전 expr · variants.

뽑기는 답 원문만 넣는다(사전 · 원장을 주지 않음). 대조는 결정론. 2자 미만 이름은 대조에서 뺀다.
원장은 `RAW_DIR` 로컬 파일(허브 원장 MCP 안 부름).
"""

from __future__ import annotations

import json
from typing import Any

from clinic_chat import prompts
from clinic_chat.clients.anthropic_client import cached_system, usage_dict
from clinic_chat.deps import NO_KEY, Deps
from clinic_chat.pricing import cost_usd

KINDS = ["시술", "제품", "장비", "부위", "성분"]
NAMES_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["names"],
    "properties": {"names": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["text", "kind"],
        "properties": {"text": {"type": "string"}, "kind": {"type": "string", "enum": KINDS}}}}},
}
MIN_LEN = 2
WHERE_MAX = 3


def final_text(msg: Any) -> str:
    return "".join(b.text for b in msg.content if b.type == "text")


async def extract_names(deps: Deps, answer: str) -> tuple[list[dict], dict, float | None]:
    """→ (이름 목록, 토큰 usage, 비용 $). 채점 provider 가 claude-cli 면 CLI(Opus · effort low · --json-schema)."""
    if deps.settings.judge_provider == "claude-cli":
        from clinic_chat.llm import cli
        from clinic_chat.llm.specs import judge_spec
        from clinic_chat.timing import CaseTimer, Clock

        c = Clock(now=deps.now)
        c.start()
        r = await cli.run(judge_spec(deps.settings, "extract"), system=prompts.load("extract"), prompt=answer,
                          timer=CaseTimer(c), on_text=lambda t: None, schema=NAMES_SCHEMA)
        if r["error"] or not isinstance(r.get("structured"), dict):
            raise RuntimeError(r["error"] or "이름 뽑기 구조화 출력 없음")
        return r["structured"].get("names", []), (r["tokens"] or {}).get("total", {}), r["cost"].get("usd_estimate")
    if deps.anthropic is None:
        raise RuntimeError(NO_KEY)
    msg = await deps.anthropic.messages.create(
        model=deps.settings.extract_model, max_tokens=4000, system=cached_system(prompts.load("extract")),
        messages=[{"role": "user", "content": answer}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": NAMES_SCHEMA}},
    )
    usage = usage_dict(msg.usage)
    return json.loads(final_text(msg))["names"], usage, cost_usd(deps.settings.extract_model, usage)


def _raw_index(deps: Deps) -> list[tuple[str, str, str]]:
    """(파일, 경로, 정규화한 문자열) — RawStore 하나에 한 번."""
    st = deps.raw_store
    idx = getattr(st, "_norm_index", None)
    if idx is None or getattr(st, "_norm_fn", None) is not deps.normalizer:
        idx = [(f, p, deps.normalizer(s)) for f, p, s in st.iter_strings()]
        st._norm_index, st._norm_fn = idx, deps.normalizer
    return idx


def _dict_index(deps: Deps) -> dict[str, str]:
    cache = getattr(deps, "_dict_norm", None)
    if cache is None:
        cache = {}
        for e in (deps.semantic or {}).get("expressions", []):
            for t in [e["expr"], *e.get("variants", [])]:
                cache.setdefault(deps.normalizer(t), e["expr"])
        deps._dict_norm = cache
    return cache


def match_names(deps: Deps, names: list[dict]) -> dict:
    """결정론 대조 — 뽑기 결과(names)만으로 다시 돌릴 수 있다."""
    if deps.raw_store is None or deps.normalizer is None:
        raise RuntimeError("RAW_DIR · SEMANTIC_JSON 필요")
    raw = _raw_index(deps)
    dic = _dict_index(deps)
    out, skipped, seen = [], [], set()
    for n in names:
        name = n["text"]
        norm = deps.normalizer(name)
        if norm in seen:
            continue
        seen.add(norm)
        if len(norm) < MIN_LEN:
            skipped.append(name)
            continue
        where = [{"file": f, "path": p} for f, p, s in raw if norm in s][:WHERE_MAX]
        via = "원장" if where else ("사전" if norm in dic else None)
        row = {"name": name, "kind": n.get("kind"), "norm": norm, "found": via is not None, "where": where, "via": via}
        if via == "사전":
            row["dict_expr"] = dic[norm]
        out.append(row)
    return {"names": out, "not_found": sum(1 for r in out if not r["found"]), "skipped": skipped}


async def check_answer(deps: Deps, answer: str) -> dict:
    names, usage, cost = await extract_names(deps, answer)
    res = match_names(deps, names)
    res["usage"] = usage
    res["cost_usd"] = cost
    res["provider"] = deps.settings.judge_provider
    res["extracted"] = names  # 뽑기 결과 그대로(다시 대조 가능)
    res["extract_model"] = deps.settings.extract_model
    return res
