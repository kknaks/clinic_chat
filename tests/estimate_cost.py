"""질문당 예상 $ · 시간 어림(모의) — 실물 허브(로컬 · 무료 · 읽기만)의 응답 크기 + 모의 Anthropic · Jev(토큰 어림 · 캐시 셈).

    uv run python -m tests.estimate_cost ["질문"] [--out 레포 밖 json]

비교: 「전」 = 캐시 없음 · 도구 결과 상한 없음 · raw_list depth 2 · case3 카드 칸 고정 / 「후」 = 지금 코드.
Anthropic · Jev 실물 호출 없음(키를 쓰지 않음). 바퀴 순서는 실물 run 926bc4477b02 꼴(case1 4바퀴 · 도구 12 / case2 resolve → recommend).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from clinic_chat.cases import case1, case2, case3, loop
from clinic_chat.clients import anthropic_client
from clinic_chat.clients.hub_mcp import HubMCP
from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.config import Settings, get_settings
from clinic_chat.deps import Deps
from clinic_chat.timing import CaseTimer, Clock
from tests.fake_anthropic import BYTES_PER_TOKEN, EstimatingAnthropic, Turn, _has_tool_result, case_of
from tests.fake_jev import FakeJev

ANSWER = ("이마 주름은 표정 근육 때문에 생기는 경우가 많아 보툴리눔 톡신 시술을 먼저 상담합니다. "
          "깊게 자리 잡은 주름은 필러를 함께 쓰기도 하며, 시술마다 금기 · 주의 사항이 있어 확인이 필요합니다. ") * 12


def _paths(params: dict) -> list[str]:
    last = params["messages"][-1]["content"]
    text = " ".join(b.get("content", "") for b in last if isinstance(b, dict)) if isinstance(last, list) else ""
    return list(dict.fromkeys(re.findall(r'"path":\s*"([^"]+)"', text)))


def policy(params: dict, n: int) -> Turn:
    c = case_of(params)
    rounds = sum(1 for m in params["messages"] if m["role"] == "assistant")
    chunks = [ANSWER[i:i + 40] for i in range(0, len(ANSWER), 40)]
    if c == "case1":
        if rounds == 0:
            return Turn(tools=[("raw_list", {}), ("raw_search", {"query": "주름"}), ("raw_search", {"query": "이마"})])
        if rounds == 1:
            return Turn(tools=[("raw_get", {"path": p}) for p in _paths(params)[:5]] or [("raw_get", {"path": "taxonomy"})])
        if rounds == 2:
            ps = _paths(params)[:2]
            return Turn(tools=[("raw_search", {"query": "보톡스"})] + [("raw_get", {"path": p}) for p in ps])
        return Turn(text=chunks)
    if c == "case2":
        if rounds == 0:
            q = params["messages"][0]["content"]
            q = q if isinstance(q, str) else q[0]["text"]
            return Turn(tools=[("resolve", {"text": q[:200]})])
        if rounds == 1 and not _has_tool_result(params) is False:
            text = json.dumps(params["messages"][-1]["content"], ensure_ascii=False)
            concerns = list(dict.fromkeys(re.findall(r"concern:[A-Za-z0-9\-]+", text))) or ["concern:P2"]
            sites = re.findall(r"site:site\.[a-z_]+", text)
            return Turn(tools=[("recommend", {"concerns": concerns[:2], **({"site": sites[0]} if sites else {})})])
        return Turn(text=chunks)
    return Turn(text=chunks)


class MixedClock:
    """실제 허브 시간(perf_counter) + 모의 LLM 지연(advance)."""

    def __init__(self) -> None:
        self.offset = 0.0

    def now(self) -> float:
        return time.perf_counter() + self.offset

    def advance(self, ms: float) -> None:
        self.offset += ms / 1000


def old_mode(stack: ExitStack) -> None:
    """「전」 꼴 — 캐시 표시 · 상한 · depth 1 · Jev 필요한 칸을 끈다."""
    orig = anthropic_client.answer_params

    def no_cache(*a, **kw):
        kw.pop("loop_cache", None)
        p = orig(*a, **kw)
        p["system"] = p["system"][0]["text"]
        p.pop("cache_control", None)
        return p

    for mod in (anthropic_client, loop, case3):
        if hasattr(mod, "answer_params"):
            stack.enter_context(mock.patch.object(mod, "answer_params", no_cache))
    stack.enter_context(mock.patch.object(case1, "RAW_LIST_DEFAULT_DEPTH", 2))
    stack.enter_context(mock.patch.object(case3, "INFO_SECTIONS", {}))


async def one_mode(question: str, label: str) -> dict:
    real = get_settings()
    s = Settings(_env_file=None, anthropic_api_key="mock", typesafe_api_key="mock", raw_dir="", semantic_json="",
                 hub_gold_mcp_url=real.hub_gold_mcp_url, hub_raw_mcp_url=real.hub_raw_mcp_url,
                 hub_rest_url=real.hub_rest_url, tool_result_max_bytes=10**9 if label == "전" else 20_000)
    out: dict = {}
    for name, mod in (("case1", case1), ("case2", case2), ("case3", case3)):
        clk = MixedClock()
        now = clk.now
        fa = EstimatingAnthropic(policy, clock=clk)
        jev = FakeJev(intent="I-01")
        import tests.fake_anthropic as fa_mod

        jev.estimate_tokens = lambda b: int(len(b) / fa_mod.BYTES_PER_TOKEN)
        deps = Deps(settings=s, anthropic=fa, jev=jev.client(s), gold_mcp=HubMCP(s.hub_gold_mcp_url),
                    raw_mcp=HubMCP(s.hub_raw_mcp_url), hub_rest=HubRest(s.hub_rest_url), now=now)
        deps.vocab = await deps.hub_rest.vocab()
        c = Clock(now=now)
        c.start()
        r = await mod.run(deps, question, CaseTimer(c), lambda t: None)
        tok = {k: sum(x[k] for x in fa.log) for k in ("input_tokens", "cache_creation_input_tokens",
                                                      "cache_read_input_tokens", "output_tokens")}
        out[name] = {"error": r.get("error"), "usd": r["cost"]["usd"], "ttft_ms": r["timing"]["ttft_ms"],
                     "total_ms": r["timing"]["total_ms"], "sonnet_calls": len(fa.log), "tokens": tok,
                     "jev_tokens": (r.get("tokens") or {}).get("jev"),
                     "tool_bytes": [(x["tool"], x["output_bytes"], x.get("truncated_to"))
                                    for x in r["trace"].get("tool_calls", [])],
                     "hub_path": (r["trace"].get("hub") or {}).get("tool")}
    out["total_usd"] = round(sum(out[c]["usd"] for c in ("case1", "case2", "case3")), 4)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?", default="이마 주름이 신경 쓰이는데 어떤 시술이 좋아요?")
    ap.add_argument("--out")
    ap.add_argument("--bytes-per-token", type=float, default=BYTES_PER_TOKEN, help="토큰 어림(민감도 보기)")
    a = ap.parse_args()
    import tests.fake_anthropic as fa_mod

    fa_mod.BYTES_PER_TOKEN = a.bytes_per_token
    res = {}
    with ExitStack() as st:
        old_mode(st)
        res["전"] = await one_mode(a.question, "전")
    res["후"] = await one_mode(a.question, "후")
    res["가정"] = {"bytes_per_token": a.bytes_per_token, "지연": "300ms + 캐시 안 된 입력 1k 토큰당 40ms + 캐시 읽기 1k 당 4ms",
                 "답 길이": len(ANSWER)}
    for k in ("전", "후"):
        r = res[k]
        print(f"[{k}] 합계 ${r['total_usd']}")
        for c in ("case1", "case2", "case3"):
            x = r[c]
            print(f"  {c}: ${x['usd']:.4f} · 첫 글자 {x['ttft_ms']} ms · 전체 {x['total_ms']} ms · Sonnet {x['sonnet_calls']}회 · "
                  f"입력 {x['tokens']['input_tokens']} · 캐시 쓰기 {x['tokens']['cache_creation_input_tokens']} · "
                  f"캐시 읽기 {x['tokens']['cache_read_input_tokens']} · 출력 {x['tokens']['output_tokens']}"
                  f"{' · Jev ' + str(x['jev_tokens']) if x['jev_tokens'] else ''} · 경로 {x['hub_path'] or '-'}"
                  f"{' · 오류 ' + x['error'] if x['error'] else ''}")
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
