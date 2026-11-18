"""상담 0.4 실측(SPEC-008 §8-1 E8-0 · WORK-017 WP-0) — 이해 호출의 thinking 끄는 법 · 지연.

    uv run python -m clinic_chat.consult.e8 --e8-0 <conversations.jsonl> [--n 20] [--mode a|b|c] [--out <jsonl>]

- 후보 ⓐ `--settings {"alwaysThinkingEnabled": false}` + env `MAX_THINKING_TOKENS=0` ⓑ `--settings` 만 ⓒ env 만 ·
  ⓓ ⓐ + env `CLAUDE_CODE_DISABLE_THINKING=1` ⓔ ⓓ + env `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1`(ⓐ ~ ⓒ 가 0 이 안 돼 더함).
- 시험 입력 = 표의 대화마다 첫 턴(앞에서 n 개) · 시스템 글 = `pick.rules` + 허브(`HUB_REST_URL`) 목록 글 · 턴 메시지 = 빈 대화 ·
  빈 객체 · 이번 말. 허브는 GET 읽기만. 결과 = 호출마다 한 줄(`ms` · `api_ms` · `usage` — thinking 칸 =
  CLI `--output-format json` 의 `usage.output_tokens_details.thinking_tokens`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics

from clinic_chat.consult import entry_list as el_mod
from clinic_chat.consult import prompt as prompt_mod
from clinic_chat.consult import state as cst
from clinic_chat.consult import understand as und
from clinic_chat.consult.understand import THINKING_OFF_ENV
from clinic_chat.llm.cli import OFF_SETTINGS
from clinic_chat.llm.cli_process import child_env

MODES = ("a", "b", "c", "d", "e")
DISABLE_ENV = {"CLAUDE_CODE_DISABLE_THINKING": "1"}  # ⓓ · ⓔ — CLI 가 읽는 env(2.1.322 바이너리에서 찾은 이름)
ADAPTIVE_ENV = {"CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1"}


def first_turns(lines: list[str], n: int) -> list[dict]:
    out = []
    for ln in lines:
        if not ln.strip():
            continue
        c = json.loads(ln)
        t = (c.get("turns") or [None])[0]
        if t:
            out.append({"conv": c["conv"], "text": t["text"]})
        if len(out) >= n:
            break
    return out


def mode_args(base: list[str], mode: str) -> list[str]:
    """`base` = thinking 인자 없는 이해 호출 인자."""
    return base + (["--settings", json.dumps(OFF_SETTINGS)] if mode in ("a", "b", "d", "e") else [])


def mode_env(mode: str) -> dict[str, str]:
    extra = dict(THINKING_OFF_ENV) if mode in ("a", "c", "d", "e") else {}
    if mode in ("d", "e"):
        extra.update(DISABLE_ENV)
    if mode == "e":
        extra.update(ADAPTIVE_ENV)
    return child_env(extra)


def _pct(xs: list[float], q: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, round(q * (len(xs) - 1))))
    return xs[k]


def summary(rows: list[dict]) -> dict:
    """호출 줄 → 요약(외부 호출 0)."""
    ok = [r for r in rows if not r.get("error")]
    th = [((r.get("usage") or {}).get("thinking_tokens")) for r in ok]
    ms = [r["ms"] for r in ok if r.get("ms") is not None]
    api = [r["api_ms"] for r in ok if r.get("api_ms") is not None]
    cache = [((r.get("usage") or {}).get("cache_read_input_tokens") or 0) for r in ok]
    return {"calls": len(rows), "errors": len(rows) - len(ok), "thinking_field": "usage.output_tokens_details.thinking_tokens",
            "thinking_nonzero": sum(1 for x in th if x), "thinking_unknown": sum(1 for x in th if x is None),
            "thinking_all_zero": bool(ok) and all(x == 0 for x in th),
            "ms_median": statistics.median(ms) if ms else None, "ms_p90": _pct(ms, 0.9),
            "api_ms_median": statistics.median(api) if api else None, "api_ms_p90": _pct(api, 0.9),
            "first_ms": ok[0]["ms"] if ok else None, "first_cache_read": cache[0] if cache else None,
            "cache_read_median": statistics.median(cache) if cache else None}


async def e80(conv_file: str, n: int, mode: str, out: str | None) -> dict:
    from clinic_chat.clients.hub_rest import HubRest
    from clinic_chat.config import load_settings

    s = load_settings()
    p = prompt_mod.load(s.chat_prompt_path())
    pick = p.pick
    assert pick, "prompt pick"
    hub = HubRest(s.hub_rest_url, page_limit=s.hub_page_limit)
    el = await el_mod.load(hub, aliases=s.chat_pick_aliases == "on", cache=False)
    system = und.system_text(pick, el)
    turns = first_turns(open(conv_file, encoding="utf-8").read().splitlines(), n)
    rows = []
    s2 = s.model_copy(update={"chat_pick_thinking": "on"})  # thinking 인자 없는 바탕 — 후보 꼴은 아래에서 더한다
    for t in turns:
        msg = und.turn_message([], cst.empty(), t["text"], pick, s2, el)
        r = await und.call(s2, system, msg, argv=mode_args(und.args(s2, system), mode), child=mode_env(mode))
        rows.append({"conv": t["conv"], "mode": mode, "ms": r.ms, "api_ms": r.api_ms, "usage": r.usage,
                     "models": r.models, "error": r.error, "out_chars": len(r.output_raw)})
        print(t["conv"], r.ms, (r.usage or {}).get("thinking_tokens"), r.error or "", flush=True)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"mode": mode, "list_items": len(el.items), "list_sha": el.sha, "gold_hash": el.gold_hash,
            "system_chars": len(system), **summary(rows)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--e8-0", dest="conv", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--mode", choices=MODES, default="a")
    ap.add_argument("--out")
    a = ap.parse_args()
    print(json.dumps(asyncio.run(e80(a.conv, a.n, a.mode, a.out)), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
