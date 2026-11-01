"""기록 검증(SPEC-006 v2 §4-4) — 배치 하나(또는 run_id 목록)를 읽어 **판단 없이** 센다. 두 번 돌리면 바이트 같음.

    uv run python -m clinic_chat.verify_runs --batch <batch_id> [--runs-file 목록.txt] [--out 폴더] [--no-hub]

| 항목 | 셈 |
|---|---|
| 잘림 | case1 · 2 도구 호출마다 기록 `truncated`(마커) · 같은 입력으로 허브 재호출(REST · 원장은 raw MCP)한 `hub_bytes` 와
        `delivered_bytes` 대조 — 허브 빌드 해시가 기록과 같을 때만(다르면 「대조 불가」) |
| intent | 스키마에 intent 있는 도구 호출 중 `intent_passed`(I-01~I-12) 비율 · 케이스 · provider · 도구별 · 넘긴 값 |
| 격리 | `stray` 호출 수 · 허브 MCP 밖 도구 이름 |
| case3 걸음 | `walk.steps_n` · `bytes_total` · `stop` · 되돌아감 · Jev 호출 · ms · peek 바이트 중앙 |
| 공정성 | 케이스별 모델이 받은 허브 바이트 합(case1 · 2 delivered 합 · case3 걸음 bytes_total + peek 합 · v1 은 hub 결과) 중앙 |
| C 비율 | 채점 실패 행(valid) 인용이 그 문항 허브 응답(재호출 글)에 있으면 C — fail-triage2 §2 와 같은 식 |

옛 기록(WORK-006 전 · 칸 없음)은 같은 뜻으로 읽는다: delivered = `output_bytes` · intent 칸 = 입력의 `intent` · 격리 밖 = 허브 ·
원장 도구 이름 밖. 출력은 레포 밖(기본 RUNS_DIR 옆 `build-out/verify-<배치>.{md,json}`) · 시간 칸은 json 머리 `meta.ran_at` 하나.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import re
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clinic_chat.cases.loop import VALID_INTENTS, stray_kind
from clinic_chat.clients.hub_mcp import HubMCP
from clinic_chat.clients.hub_rest import HubRest, HubRestError
from clinic_chat.config import ROOT, TRUNCATION_MARKERS, get_settings, truncation_markers_sha
from clinic_chat.runs import QUALITY_FILE, RUNS_FILE

GOLD_TOOLS = ("resolve", "recommend", "procedure_card", "pair_check", "check_conditions", "compare", "combos",
              "neighbors", "vocab")
WALK_TOOLS = ("peek", "expand")
RAW_TOOLS = ("raw_list", "raw_get", "raw_search")
HUB_TOOLS = GOLD_TOOLS + WALK_TOOLS
# 옛 기록의 intent 칸 — 허브 v3.1 은 골드 도구 9 모두 스키마에 intent(앱 기동 때 intent_tools 와 같음)
OLD_INTENT_TOOLS = frozenset(GOLD_TOOLS)
# REST 글과 MCP 로 넘어간 글의 작은 차이(직렬화) — 이 바이트 안이면 잘림 아님(fail-triage2 §5 와 같은 값)
BYTES_SLACK = 8
CASES = ("case1", "case2", "case3")
ITEM = {"accuracy": "정확성", "safety": "안전", "candidates": "후보", "uncertainty": "불확실", "on_topic": "질문에 답함"}


# ── 읽기 ─────────────────────────────────────────────────────────────────────
def load_runs(runs_dir: Path, batch: str | None, run_ids: list[str] | None) -> list[dict]:
    out: dict[str, dict] = {}
    for line in (runs_dir / RUNS_FILE).read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out[d["run_id"]] = d
    if run_ids is not None:
        keep = set(run_ids)
        rows = [d for d in out.values() if d["run_id"] in keep]
    else:
        rows = [d for d in out.values() if d.get("batch_id") == batch]
    return sorted(rows, key=lambda d: (str(d.get("question_no") or ""), d["run_id"]))


def load_quality(runs_dir: Path, run_ids: set[str]) -> list[dict]:
    p = runs_dir / QUALITY_FILE
    if not p.is_file():
        return []
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [q for q in rows if q.get("run_id") in run_ids]


def trace(rec: dict, case: str) -> dict:
    return ((rec.get("cases") or {}).get(case) or {}).get("trace") or {}


def delivered(c: dict) -> int | None:
    v = c.get("delivered_bytes", c.get("output_bytes"))
    return v if isinstance(v, int) else None


def intent_passed(c: dict) -> bool | None:
    if "intent_passed" in c:
        return c["intent_passed"]
    if c.get("tool") not in OLD_INTENT_TOOLS:
        return None
    inp = c.get("input")
    return isinstance(inp, dict) and inp.get("intent") in VALID_INTENTS


def is_stray(c: dict) -> bool:
    if "stray" in c:
        return bool(c["stray"])
    return c.get("tool") not in HUB_TOOLS + RAW_TOOLS


def walk_calls(w: dict) -> list[tuple[str, dict, str]]:
    """걸음 기록 → 재호출 목록 (도구, 입력, 몫 peek|expand|list|full). v2.4 기록은 호출마다 보낸 본문(`input`) 그대로 ·
    peek 재사용 걸음은 허브 호출 없음 · 옛 기록(input 칸 없음)은 sections · neighbors 로."""
    out = []
    start = w.get("start_node")
    for kd in (w.get("list") or {}).get("kinds") or []:
        out.append(("expand", {"id": start, "neighbor_kinds": [kd]}, "list"))
    for st in w.get("steps") or []:
        if st.get("node") and not st.get("peek_reused") and not st.get("error", "").startswith("Jev"):
            out.append(("peek", {"id": st["node"]}, "peek"))
        ex = st.get("expand") or {}
        # 섹션별 호출 기록(calls) — 예산 넘어 뺀 호출은 모델이 안 받았으니 뺌
        for c in [c for c in ex.get("calls") or [ex] if c.get("excluded") is not True]:
            if c.get("input"):
                out.append(("expand", {"id": st["node"], **c["input"]}, "expand"))
            elif c.get("sections") or c.get("neighbors"):
                out.append(("expand", {"id": st["node"], "sections": c.get("sections") or None,
                                       "neighbors": c.get("neighbors") or None}, "expand"))
    for c in (w.get("full") or {}).get("calls_detail") or []:
        if c.get("input"):
            out.append(("expand", {"id": c["node"], **c["input"]}, "full"))
    return out


def p95(xs: list[int | float]) -> int | None:
    xs = sorted(x for x in xs if isinstance(x, (int, float)))
    return int(xs[min(len(xs) - 1, round(0.95 * (len(xs) - 1)))]) if xs else None


COVER34_FILE = ROOT / "clinic_chat" / "walk_cover34.json"


def _step_facts(st: dict) -> list[str]:
    return list((st.get("expand") or {}).get("fact_ids") or [])


def cover34(runs: list[dict]) -> dict:
    """§3-4-8 · §7-8 34 행 — 1홉 몫(목표를 1쪽에 가진 이웃 ∈ walk.step1_options) · 0홉 몫(그 section ∈ 선택지) ·
    S 가 rerun-v4 와 같은 행만 문턱 분모 · 다른 행은 보고 · 실제로 고른 행 · 받은 행."""
    rows = json.loads(COVER34_FILE.read_text(encoding="utf-8"))["rows"]
    by_no = {r.get("question_no"): r for r in runs if r.get("question_no")}
    out = {}
    for place, key in (("1홉·1쪽", "one_hop"), ("0홉·1쪽", "zero_hop")):
        rs = [x for x in rows if x["place"] == place]
        det = []
        for x in rs:
            rec = by_no.get(x["no"])
            w = trace(rec, "case3").get("walk") if rec else None
            if not w:
                det.append({"no": x["no"], "run": bool(rec), "same_start": None})
                continue
            same = w.get("start_node") == x["start"]
            opts = set(w.get("step1_options") or [])
            st1 = (w.get("steps") or [{}])[0]
            j1 = st1.get("jev") or {}
            if key == "one_hop":
                tg = {n for ns in (x.get("targets") or {}).values() for n in ns}
                shown = bool(tg & opts)
                picked = bool(tg & set(j1.get("picked_neighbors") or []))
                got = any(st.get("node") in tg and _step_facts(st) for st in w.get("steps") or [])
            else:
                secs = set(x.get("sections") or [])
                shown = bool({"section:" + y for y in secs} & opts)
                picked = bool(secs & set(j1.get("picked_sections") or []))
                got = any(st.get("node") == x["start"] and set((st.get("expand") or {}).get("sections") or []) & secs
                          for st in w.get("steps") or [])
            det.append({"no": x["no"], "run": True, "same_start": same, "start": w.get("start_node"), "shown": shown,
                        "picked": picked, "got": got, "fallback": bool(w.get("fallback"))})
        same = [d for d in det if d.get("same_start")]
        out[key] = {"rows": len(rs), "denominator": len(same), "shown": sum(d["shown"] for d in same),
                    "picked": sum(d["picked"] for d in same), "got": sum(d["got"] for d in same),
                    "other_start": [d["no"] for d in det if d.get("same_start") is False],
                    "other_start_shown": sum(1 for d in det if d.get("same_start") is False and d.get("shown")),
                    "not_run": [d["no"] for d in det if not d.get("run")], "detail": det}
    out["sum_shown"] = out["one_hop"]["shown"] + out["zero_hop"]["shown"]
    return out


def walk_summary(runs: list[dict]) -> dict | None:
    """case3 걸음 셈(§4-4 v2.2~v2.4 · §7-8) — 판 단위 · 182 기준(되돌아간 판 포함) · 이웃 fact 도달은 걸음 판 기준."""
    ws = [(r, trace(r, "case3").get("walk")) for r in runs]
    ws = [(r, w) for r, w in ws if w]
    if not ws:
        return None
    n = len(ws)
    walked = [w for _, w in ws if not w.get("fallback")]
    stop = collections.Counter(str(w.get("stop")) for _, w in ws)

    def nb_fact(w: dict) -> bool:
        return any(st.get("node") != w.get("start_node") and _step_facts(st) for st in w.get("steps") or [])

    def nb_peek(w: dict) -> bool:
        return len({st.get("node") for st in w.get("steps") or [] if st.get("node")}) >= 2

    def hop2(w: dict) -> bool:
        return any((st.get("hop") or 0) >= 2 and _step_facts(st) for st in w.get("steps") or [])

    steps = [st for _, w in ws for st in w.get("steps") or []]
    calls = [c for st in steps for c in (st.get("expand") or {}).get("calls") or []]
    fulls = [w.get("full") for _, w in ws if w.get("full")]
    return {
        "runs": n, "walked": len(walked), "fallback": n - len(walked),
        "fallback_reasons": dict(sorted(collections.Counter(str(w.get("fallback_reason")) for _, w in ws
                                                            if w.get("fallback")).items())),
        "steps_median": med([w.get("steps_n") or 0 for _, w in ws]),
        "steps_n": dict(sorted(collections.Counter(str(w.get("steps_n")) for _, w in ws).items())),
        "stop": dict(sorted(stop.items())), "budget_stop": stop.get("budget", 0),
        "budget_stop_ratio": round(stop.get("budget", 0) / n, 4),
        "neighbor_fact_reach": sum(nb_fact(w) for w in walked), "neighbor_peek_reach": sum(nb_peek(w) for w in walked),
        "hop2_fact_reach": sum(hop2(w) for w in walked),
        "bytes_total_median": med([w["bytes_total"] for w in walked if isinstance(w.get("bytes_total"), int)]),
        "bytes_total_p95": p95([w["bytes_total"] for w in walked if isinstance(w.get("bytes_total"), int)]),
        "full_bytes_median": med([f.get("bytes") or 0 for f in fulls]), "full_bytes_p95": p95([f.get("bytes") or 0 for f in fulls]),
        "full_over_budget": sum(1 for f in fulls if f.get("over_budget")),
        "full_cut_unfetched": sum(len(f.get("cut_unfetched_ids") or []) for f in fulls),
        "list_calls": sum((w.get("list") or {}).get("calls") or 0 for _, w in ws),
        "list_bytes_median": med([(w.get("list") or {}).get("bytes") for _, w in ws if (w.get("list") or {}).get("calls")]),
        "list_ms_median": med([(w.get("list") or {}).get("ms") for _, w in ws if (w.get("list") or {}).get("calls")]),
        "excluded": sum(1 for c in calls if c.get("excluded")),
        "filtered_by_budget": sum(len((st.get("jev") or {}).get("filtered_by_budget") or []) for st in steps),
        "bytes_mismatch": sum(w.get("bytes_mismatch") or 0 for _, w in ws),
        "peek_reused": sum(1 for st in steps if st.get("peek_reused")),
        "by_unit_queued": sum(len(w.get("by_unit_queued") or []) for _, w in ws),
        "jev_calls": sum(w.get("jev_calls") or 0 for _, w in ws),
        "jev_ms_median": med([w["jev_ms_total"] for _, w in ws if isinstance(w.get("jev_ms_total"), (int, float))]),
        "peek_bytes_median": med([st["peek_bytes"] for st in steps if isinstance(st.get("peek_bytes"), int)]),
        "order_table_sha": sorted({str(w.get("order_table_sha")) for _, w in ws}),
        # 걷기 고침 2 — 필수 묶음 · 고정 섹션 · 「충분」 무시
        "required_runs": sum(1 for w in walked if (w.get("required") or {}).get("fact_ids")),
        "required_facts": sum(len((w.get("required") or {}).get("fact_ids") or []) for w in walked),
        "required_bytes_median": med([(w.get("required") or {}).get("bytes") or 0 for w in walked if w.get("required")]),
        "required_filtered": sum(len((w.get("required") or {}).get("filtered") or []) for w in walked),
        "fixed_runs": sum(1 for w in walked if (w.get("fixed") or {}).get("sections")),
        "fixed_filtered": sum(len((w.get("fixed") or {}).get("filtered") or []) for w in walked),
        "enough_ignored": sum(w.get("enough_ignored") or 0 for _, w in ws),
        # 걷기 고침 3 — 의도 묶음 바닥 · 남은 예산 채우기 · 판마다 받은 섹션 수(걸음 + 채우기)
        "base_bundle_runs": sum(1 for w in walked if (w.get("base_bundle") or {}).get("sections")),
        "bundle_sections": sum(len(b.get("sections") or []) for w in walked for b in w.get("bundles") or []),
        "fill_runs": sum(1 for w in walked if (w.get("fill") or {}).get("sections")),
        "fill_bytes_median": med([(w.get("fill") or {}).get("bytes") or 0 for w in walked if w.get("fill")]),
        "fill_sections": sum(len((w.get("fill") or {}).get("sections") or []) for w in walked),
        "sections_per_run_median": med([sum(len((st.get("expand") or {}).get("sections") or []) for st in w.get("steps") or [])
                                        + len([x for x in (w.get("fill") or {}).get("sections") or [] if not x.get("excluded")])
                                        + len([c for c in (w.get("required") or {}).get("calls") or [] if not c.get("excluded")])
                                        for w in walked]),
    }


def case_stats(runs: list[dict], case: str) -> dict:
    """보고서용 기록 칸 셈(허브 재호출 없이) — intent 전달 · 마커 잘림 · 격리 밖 · 받은 허브 바이트 · case3 걸음."""
    ok = n = marked = stray = 0
    got: list[int] = []
    for r in runs:
        tr = trace(r, case)
        tot = None
        for c in tr.get("tool_calls") or []:
            if is_stray(c):
                stray += 1
                continue
            ip = intent_passed(c)
            if ip is not None:
                n += 1
                ok += int(bool(ip))
            marked += int(c.get("truncated") is True)
            if delivered(c) is not None:
                tot = (tot or 0) + delivered(c)
        w = tr.get("walk")
        if case == "case3":  # 앱이 넘기는 intent — 걸음 판 walk.intent · v1 판 허브 경로 입력(판마다 1)
            hub = tr.get("hub") or {}
            iv = w.get("intent") if w else ((hub.get("input") or {}).get("intent") if hub.get("tool") else None)
            if w or hub.get("tool"):
                n += 1
                ok += int(iv in VALID_INTENTS)
            tot = ((w.get("bytes_total") or 0) + sum((s.get("peek_bytes") or 0) for s in w.get("steps") or [])
                   if w else (tr.get("hub") or {}).get("result_bytes"))
        if isinstance(tot, int):
            got.append(tot)
    by_kind = collections.Counter()
    for r in runs:
        for c in trace(r, case).get("tool_calls") or []:
            if is_stray(c):
                by_kind[c.get("stray_kind") or stray_kind(str(c.get("tool")))] += 1
    return {"intent_passed": ok, "intent_calls": n, "intent_ratio": round(ok / n, 4) if n else None,
            "truncated_marker": marked, "stray": stray, "stray_by_kind": {k: by_kind.get(k, 0) for k in ("bare_hub", "foreign")},
            "hub_bytes_median": med(got), "walk": walk_summary(runs) if case == "case3" else None}


def call_key(tool: str, args: Any) -> str:
    return hashlib.md5(json.dumps([tool, args], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ── 허브 재호출 ───────────────────────────────────────────────────────────────
class Replay:
    """같은 입력 재호출(결정론 · 같은 키는 한 번). 결과 = {status, text}. 허브 없음이면 비움."""

    def __init__(self, rest: HubRest | None, raw: HubMCP | None, conc: int = 8) -> None:
        self.rest, self.raw, self.conc = rest, raw, conc
        self.calls: dict[str, dict] = {}
        self.want: dict[str, tuple[str, Any]] = {}

    def need(self, tool: str, args: Any) -> str:
        k = call_key(tool, args)
        self.want.setdefault(k, (tool, args))
        return k

    async def run(self) -> None:
        sem = asyncio.Semaphore(self.conc)
        todo = sorted(k for k in self.want if k not in self.calls)

        async def one(k: str) -> None:
            tool, args = self.want[k]
            async with sem:
                self.calls[k] = await self._one(tool, args)

        rest_keys = [k for k in todo if self.want[k][0] not in RAW_TOOLS]
        raw_keys = [k for k in todo if self.want[k][0] in RAW_TOOLS]
        await asyncio.gather(*(one(k) for k in rest_keys))
        if raw_keys and self.raw is not None:
            try:
                async with self.raw.session() as s:
                    for k in raw_keys:  # 세션 하나 · 차례로
                        tool, args = self.want[k]
                        r = await s.call(tool, dict(args or {}))
                        self.calls[k] = {"status": 200 if not r.is_error else -4, "text": r.text}
            except Exception as e:  # noqa: BLE001 — 원장 MCP 못 붙음 = 그 호출들만 허브 오류
                for k in raw_keys:
                    self.calls.setdefault(k, {"status": -1, "text": f"{type(e).__name__}: {e}"})

    async def _one(self, tool: str, args: Any) -> dict:
        if tool in RAW_TOOLS:
            return {"status": -5, "text": "원장 MCP 없음"}
        if self.rest is None:
            return {"status": -5, "text": "허브 없음"}
        try:
            st, txt = await self.rest.call_status(tool, dict(args or {}))
        except HubRestError as e:
            return {"status": -2, "text": str(e)}
        except Exception as e:  # noqa: BLE001 — 전송 오류도 그 호출만
            return {"status": -1, "text": f"{type(e).__name__}: {e}"}
        return {"status": st, "text": txt}

    def get(self, k: str | None) -> dict | None:
        return self.calls.get(k) if k else None


# ── C 판정(fail-triage2 §2 와 같은 식 · 원장 · 골드 export 없이 허브 응답 글만) ─────────────
def norm(s: Any) -> str:
    return re.sub(r"[\s\W_]+", "", str(s).lower())


def _flat(o: Any):
    if isinstance(o, dict):
        for v in o.values():
            yield from _flat(v)
    elif isinstance(o, list):
        for v in o:
            yield from _flat(v)
    elif isinstance(o, str) and len(o) >= 2:
        yield o


class HubText:
    """한 문항 · 한 케이스가 받은 허브 응답(재호출 글) — 정규화 전체 · 문자열 칸 · facts[].text."""

    def __init__(self, texts: list[str]) -> None:
        self.norm = norm("".join(texts))
        self.strs: set[str] = set()
        self.facts: set[str] = set()
        for t in texts:
            try:
                o = json.loads(t)
            except ValueError:
                continue
            self.strs |= {norm(s) for s in _flat(o)}
            if isinstance(o, dict):
                self.facts |= {norm(f.get("text", "")) for f in o.get("facts") or [] if isinstance(f, dict)}
        self.strs.discard("")
        self.facts.discard("")

    def has(self, quote: str) -> bool | None:
        q = norm(quote)
        if len(q) < 4:
            return None  # 인용이 너무 짧음 — 대조 불가(미분류)
        if len(q) >= 6 and q in self.norm:
            return True
        if any(len(s) >= 8 and s in q for s in self.strs):
            return True
        return any(q in f and (len(q) >= 8 or len(f) <= 2 * len(q) + 4) for f in self.facts)


# ── 셈 ───────────────────────────────────────────────────────────────────────
def forms(text: str) -> tuple[int, ...]:
    """같은 응답의 바이트 꼴 — 받은 글 그대로 · 압축 JSON(MCP 가 넘긴 꼴이 서버마다 다름: 골드 REST 압축 · 원장 MCP 들여쓰기)."""
    b = len(text.encode())
    try:
        o = json.loads(text)
    except ValueError:
        return (b,)
    n = _int_floats(o)  # 14.0 → 14(같은 값 · MCP 쪽 직렬화가 정수로 씀 — rerun-v4 pair_check · compare 실측)
    return b, *(len(json.dumps(x, ensure_ascii=False, separators=sep).encode())
                for x in (o, n) for sep in ((", ", ": "), (",", ":")))


def _int_floats(o: Any) -> Any:
    if isinstance(o, float) and o.is_integer():
        return int(o)
    if isinstance(o, dict):
        return {k: _int_floats(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_int_floats(v) for v in o]
    return o


def pct(a: int, b: int) -> str:
    return f"{a / b:.1%}" if b else "–"


def med(xs: list[int | float]) -> int | None:
    return int(statistics.median(xs)) if xs else None


def verify(runs: list[dict], quality: list[dict], rp: Replay, hub_hash: str | None) -> dict:
    """재호출이 끝난 Replay 로 센다(순수 · 결정론)."""
    trunc_rows: list[dict] = []
    intent_c: dict[tuple, list[int]] = collections.defaultdict(lambda: [0, 0])
    intent_vals: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    stray_c: collections.Counter = collections.Counter()
    stray_names: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    stray_kinds: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
    stray_rows: list[dict] = []
    fair: dict[tuple, list[int]] = collections.defaultdict(list)
    texts: dict[tuple[str, str], list[str]] = {}
    walk_rows: list[dict] = []
    compare_ok = collections.Counter()
    for rec in runs:
        prov = rec.get("provider") or "sdk"
        rec_hash = (rec.get("versions") or {}).get("gold_build_hash")
        same_build = bool(hub_hash) and rec_hash == hub_hash
        compare_ok["같음" if same_build else "다름"] += 1
        for case in ("case1", "case2"):
            calls = trace(rec, case).get("tool_calls") or []
            got: list[str] = []
            total = 0
            for ci, c in enumerate(calls):
                tool = c.get("tool")
                if is_stray(c):
                    if "stray_fixed_next" not in c:  # 옛 기록 — 바로 다음 호출이 같은 도구 제 이름인가(§3-8 사실 3)
                        nx = calls[ci + 1] if ci + 1 < len(calls) else None
                        c = {**c, "stray_fixed_next": bool(nx and not is_stray(nx) and (
                            nx.get("tool") == tool or str(tool).startswith(str(nx.get("tool")))))}
                    stray_c[(case, prov)] += 1
                    stray_names[(case, prov)][str(tool)] += 1
                    stray_kinds[(case, prov)][c.get("stray_kind") or stray_kind(str(tool))] += 1
                    stray_rows.append({"no": rec.get("question_no"), "case": case, "provider": prov, "tool": tool,
                                       "kind": c.get("stray_kind") or stray_kind(str(tool)),
                                       "fixed_next": c.get("stray_fixed_next")})
                    continue
                ip = intent_passed(c)
                if ip is not None:
                    intent_c[(case, prov, tool)][1] += 1
                    intent_c[(case, prov, tool)][0] += int(bool(ip))
                    inp = c.get("input")
                    if isinstance(inp, dict) and "intent" in inp:
                        intent_vals[(case, prov)][str(inp["intent"])] += 1
                d = delivered(c)
                total += d or 0
                row = {"run_id": rec["run_id"], "no": rec.get("question_no"), "case": case, "provider": prov,
                       "tool": tool, "delivered_bytes": d, "marker": c.get("truncated"), "rec_error": bool(c.get("is_error"))}
                k = rp.need(tool, c.get("input")) if same_build else None
                row["key"] = k
                trunc_rows.append(row)
                got.append(k or "")
            fair[(case, prov)].append(total)
            texts[(rec["run_id"], case)] = got if same_build else None
        tr3 = trace(rec, "case3")
        w = tr3.get("walk")
        keys3: list[str] = []
        if w:
            peek_b = sum((s.get("peek_bytes") or 0) for s in w.get("steps") or [])
            fair[("case3", prov)].append((w.get("bytes_total") or 0) + peek_b)
            walk_rows.append({"run_id": rec["run_id"], "no": rec.get("question_no"), "provider": prov,
                              "steps_n": w.get("steps_n"), "bytes_total": w.get("bytes_total"), "stop": w.get("stop"),
                              "fallback": bool(w.get("fallback")), "fallback_reason": w.get("fallback_reason"),
                              "jev_calls": w.get("jev_calls"), "jev_ms_total": w.get("jev_ms_total"),
                              "hub_ms_total": w.get("hub_ms_total"),
                              "peek_bytes": [s.get("peek_bytes") for s in w.get("steps") or []
                                             if isinstance(s.get("peek_bytes"), int)]})
            if same_build:
                keys3 = [rp.need(t, a) for t, a, _ in walk_calls(w)]
        hub = tr3.get("hub") or {}
        if hub.get("tool") and (not w or w.get("fallback")):
            if not w and isinstance(hub.get("result_bytes"), int):
                fair[("case3", prov)].append(hub["result_bytes"])
            if same_build:
                keys3.append(rp.need(hub["tool"], hub.get("input")))
        texts[(rec["run_id"], "case3")] = keys3 if same_build else None
    return {"trunc_rows": trunc_rows, "intent_c": intent_c, "intent_vals": intent_vals, "stray_c": stray_c,
            "stray_names": stray_names, "stray_kinds": stray_kinds, "stray_rows": stray_rows, "fair": fair, "texts": texts, "walk_rows": walk_rows,
            "compare_ok": compare_ok, "quality": quality}


def finish(acc: dict, rp: Replay, runs: list[dict], meta: dict) -> dict:
    """재호출 결과를 붙여 표 · json 한 벌."""
    by_run = {r["run_id"]: r for r in runs}
    # 잘림
    t_rows = []
    for r in acc["trunc_rows"]:
        hit = rp.get(r["key"])
        hb = len(hit["text"].encode()) if hit and hit["status"] == 200 else None
        d = r["delivered_bytes"]
        if r["key"] is None:
            v = "대조 불가"
        elif hit is None or hit["status"] != 200:
            v = "허브 오류"
        elif r["marker"] is True:
            v = "잘림(마커)"
        elif d is None:
            v = "기록 없음"
        elif r["rec_error"] and d not in forms(hit["text"]):
            v = "기록 오류"  # 그때 도구가 오류를 냄(지금 재호출은 200) — 잘림 아님
        elif d in forms(hit["text"]):
            v = "같음"
        elif d < min(forms(hit["text"])) - BYTES_SLACK:
            v = "잘림(unknown)"
        else:
            v = "근소 차이"
        t_rows.append({**{k: r[k] for k in ("no", "case", "provider", "tool", "delivered_bytes", "marker", "rec_error")},
                       "hub_bytes": hb, "verdict": v})
    trunc = {}
    for case in ("case1", "case2"):
        for prov in sorted({r["provider"] for r in t_rows if r["case"] == case}):
            rs = [r for r in t_rows if r["case"] == case and r["provider"] == prov]
            vc = collections.Counter(r["verdict"] for r in rs)
            cut = [r for r in rs if r["verdict"].startswith("잘림")]
            trunc[f"{case}/{prov}"] = {
                "calls": len(rs), "ok200": sum(1 for r in rs if r["hub_bytes"] is not None),
                **{k: vc.get(k, 0) for k in ("같음", "근소 차이", "잘림(마커)", "잘림(unknown)", "대조 불가", "허브 오류",
                                              "기록 없음", "기록 오류")},
                "truncated": len(cut), "by_tool": dict(collections.Counter(r["tool"] for r in cut).most_common()),
                "delivered_median": med([r["delivered_bytes"] for r in cut if r["delivered_bytes"] is not None]),
                "hub_median": med([r["hub_bytes"] for r in cut if r["hub_bytes"] is not None])}
    # intent
    intent = []
    for (case, prov, tool), (ok, n) in sorted(acc["intent_c"].items()):
        intent.append({"case": case, "provider": prov, "tool": tool, "passed": ok, "calls": n})
    intent_sum = {}
    for case, prov in sorted({(x["case"], x["provider"]) for x in intent}):
        rs = [x for x in intent if x["case"] == case and x["provider"] == prov]
        ok, n = sum(x["passed"] for x in rs), sum(x["calls"] for x in rs)
        vals = acc["intent_vals"].get((case, prov), collections.Counter())
        intent_sum[f"{case}/{prov}"] = {"passed": ok, "calls": n, "ratio": round(ok / n, 4) if n else None,
                                         "given": sum(vals.values()), "values": dict(sorted(vals.items()))}
    stray = {f"{c}/{p}": {"n": n, "names": dict(sorted(acc["stray_names"][(c, p)].items())),
                          "by_kind": {k: acc["stray_kinds"][(c, p)].get(k, 0) for k in ("bare_hub", "foreign")},
                          "fixed_next": sum(1 for x in acc["stray_rows"] if (x["case"], x["provider"]) == (c, p)
                                            and x["fixed_next"]),
                          "questions": sorted({str(x["no"]) for x in acc["stray_rows"] if (x["case"], x["provider"]) == (c, p)})}
             for (c, p), n in sorted(acc["stray_c"].items())}
    # 걸음(§4-4 v2.2~v2.4)
    walk = walk_summary(runs)
    if walk:
        walk["cover34"] = cover34(runs)
    fair = {f"{c}/{p}": {"runs": len(xs), "median": med(xs)} for (c, p), xs in sorted(acc["fair"].items())}
    # C 비율
    rows_c = []
    invalid = collections.Counter()
    for q in sorted(acc["quality"], key=lambda q: (str(q.get("question_no") or ""), q["case"], q.get("at") or "")):
        rec = by_run.get(q["run_id"])
        case = q["case"]
        prov = (rec or {}).get("provider") or "sdk"
        keys = acc["texts"].get((q["run_id"], case))
        if keys is None:  # 빌드 다름 · 허브 없음
            ht = None
        else:
            got = [rp.get(k) for k in keys if k]
            ht = HubText([g["text"] for g in got if g and g["status"] == 200])
        for f in (q.get("judge") or {}).get("failures") or []:
            if f.get("status") != "valid":
                invalid[case] += 1
                continue
            quote = (f.get("evidence") or {}).get("quote") or ""
            hit = ht.has(quote) if ht else None
            rows_c.append({"no": q.get("question_no"), "case": case, "provider": prov, "item": f.get("item"),
                           "path": (f.get("evidence") or {}).get("path"), "c": hit,
                           "comparable": ht is not None})
    c_sum = {}
    for case, prov in sorted({(r["case"], r["provider"]) for r in rows_c}):
        rs = [r for r in rows_c if r["case"] == case and r["provider"] == prov]
        c = sum(1 for r in rs if r["c"] is True)
        c_sum[f"{case}/{prov}"] = {"fail_rows": len(rs), "C": c, "ratio": round(c / len(rs), 4) if rs else None,
                                    "short_quote": sum(1 for r in rs if r["comparable"] and r["c"] is None),
                                    "not_comparable": sum(1 for r in rs if not r["comparable"])}
    gold23 = [r for r in rows_c if r["case"] in ("case2", "case3")]
    c23 = sum(1 for r in gold23 if r["c"] is True)
    return {"meta": meta, "build_compare": dict(sorted(acc["compare_ok"].items())), "truncation": trunc,
            "intent": intent_sum, "intent_by_tool": intent, "stray": stray, "walk": walk, "fairness": fair,
            "c_ratio": c_sum, "c_case23": {"C": c23, "fail_rows": len(gold23)}, "invalid_excluded": dict(sorted(invalid.items())),
            "c_rows": [r for r in rows_c if r["c"] is True], "trunc_rows": [r for r in t_rows if r["verdict"] != "같음"]}


def render(J: dict) -> str:
    m = J["meta"]
    L = [f"# 기록 검증 — {m['label']}", "",
         (f"판 {m['runs']} · 허브 빌드 {m['hub_build_hash']} · 기록 빌드와 같음 {J['build_compare'].get('같음', 0)} · "
          f"다름 {J['build_compare'].get('다름', 0)}(다르면 재호출 안 함 = 대조 불가) · 마커 {m['truncation_markers_sha']} · "
          f"잘림 = delivered < hub − {BYTES_SLACK}B 또는 마커"), ""]
    L += ["## 잘림(case1 · 2)", "", ("| 케이스/provider | 호출 | 허브 200 | 같음 | 근소 차이 | 잘림(마커) | 잘림(unknown) | 대조 불가 | "
                                    "허브 오류 | 기록 없음 | 기록 오류 | 잘린 도구 | 받은 중앙 B | 허브 중앙 B |"), "|" + "---|" * 14]
    for k, t in J["truncation"].items():
        L.append(f"| {k} | {t['calls']} | {t['ok200']} | {t['같음']} | {t['근소 차이']} | {t['잘림(마커)']} | {t['잘림(unknown)']} | "
                 f"{t['대조 불가']} | {t['허브 오류']} | {t['기록 없음']} | {t['기록 오류']} | "
                 f"{' · '.join(f'{a} {b}' for a, b in t['by_tool'].items())} | {t['delivered_median']} | {t['hub_median']} |")
    L += ["", "## intent 전달(스키마에 intent 있는 도구)", "", "| 케이스/provider | 넘김(I-01~12) | 해당 호출 | 비율 | intent 칸 있음 | 값 |",
          "|---|---|---|---|---|---|"]
    for k, t in J["intent"].items():
        L.append(f"| {k} | {t['passed']} | {t['calls']} | {pct(t['passed'], t['calls'])} | {t['given']} | "
                 f"{' · '.join(f'{a} {b}' for a, b in t['values'].items())} |")
    L += ["", "도구별:", "", "| 케이스/provider | 도구 | 넘김 | 호출 | 비율 |", "|---|---|---|---|---|"]
    for x in J["intent_by_tool"]:
        L.append(f"| {x['case']}/{x['provider']} | {x['tool']} | {x['passed']} | {x['calls']} | {pct(x['passed'], x['calls'])} |")
    L += ["", "## 격리 밖 도구(stray · §3-8 bare_hub = 접두 없는 허브 이름 · foreign = 그 밖)", ""]
    if J["stray"]:
        L += ["| 케이스/provider | 호출 | bare_hub | foreign | 다음 호출이 고쳐 부름 | 이름 | 문항 |", "|---|---|---|---|---|---|---|"]
        for k, t in J["stray"].items():
            L.append(f"| {k} | {t['n']} | {t['by_kind']['bare_hub']} | {t['by_kind']['foreign']} | {t['fixed_next']} | "
                     f"{' · '.join(f'{a} {b}' for a, b in t['names'].items())} | {' · '.join(t['questions'])} |")
    else:
        L.append("0")
    L += ["", "## case3 걸음(§4-4 · §7-8)", ""]
    w = J["walk"]
    if w:
        n, wn = w["runs"], w["walked"]
        L += ["| 칸 | 값 |", "|---|---|",
              f"| 판 · 걸음 판 · 되돌아감(사유) | {n} · {wn} · {w['fallback']} {w['fallback_reasons']} |",
              f"| 걸음 수 중앙(판 {n} 기준 · 분포) | {w['steps_median']} · {w['steps_n']} |",
              f"| 멈춤 · 예산 멈춤 비율 | {w['stop']} · {w['budget_stop']}/{n} = {pct(w['budget_stop'], n)} |",
              (f"| 이웃 fact 도달 · 이웃 peek 도달 · 2홉 fact 도달(걸음 판 기준) | {w['neighbor_fact_reach']}/{wn} = "
               f"{pct(w['neighbor_fact_reach'], wn)} · {w['neighbor_peek_reach']}/{wn} = {pct(w['neighbor_peek_reach'], wn)} · "
               f"{w['hop2_fact_reach']}/{wn} |"),
              f"| bytes_total 중앙 · p95 | {w['bytes_total_median']} · {w['bytes_total_p95']} |",
              (f"| full.bytes 중앙 · p95 · 원문 예산 넘은 판 · 못 받은 cut 줄 | {w['full_bytes_median']} · {w['full_bytes_p95']} · "
               f"{w['full_over_budget']} · {w['full_cut_unfetched']} |"),
              f"| 목록 호출(neighbor_kinds) 합 · 바이트 중앙 · ms 중앙 | {w['list_calls']} · {w['list_bytes_median']} · {w['list_ms_median']} |",
              (f"| excluded · filtered_by_budget · bytes_mismatch · peek 재사용 · by_unit 대기열 | {w['excluded']} · "
               f"{w['filtered_by_budget']} · {w['bytes_mismatch']} · {w['peek_reused']} · {w['by_unit_queued']} |"),
              (f"| 필수 묶음 판 · fact · 바이트 중앙 · 예산으로 뺌 / 고정 섹션 판 · 뺌 / 「충분」 무시 | {w['required_runs']} · "
               f"{w['required_facts']} · {w['required_bytes_median']} · {w['required_filtered']} / {w['fixed_runs']} · "
               f"{w['fixed_filtered']} / {w['enough_ignored']} |"),
              (f"| 의도 묶음 판 · 묶음 섹션 / 채우기 판 · 섹션 · 바이트 중앙 / 판마다 받은 섹션 중앙 | {w['base_bundle_runs']} · "
               f"{w['bundle_sections']} / {w['fill_runs']} · {w['fill_sections']} · {w['fill_bytes_median']} / "
               f"{w['sections_per_run_median']} |"),
              (f"| Jev 호출 합 · Jev ms 중앙 · peek 바이트 중앙 · 순서 표 판 | {w['jev_calls']} · {w['jev_ms_median']} · "
               f"{w['peek_bytes_median']} · {' · '.join(w['order_table_sha'])} |")]
        c = w.get("cover34")
        if c:
            L += ["", "34 행(§3-4-8 · 받는 자리 · S 가 rerun-v4 와 같은 행만 분모):", "",
                  "| 몫 | 행 | 분모(S 같음) | 선택지에 뜸 | 고름 | 받음 | S 다른 행(뜸) | 판 없음 |", "|---|---|---|---|---|---|---|---|"]
            for key, nm in (("one_hop", "1홉·1쪽"), ("zero_hop", "0홉·1쪽")):
                x = c[key]
                L.append(f"| {nm} | {x['rows']} | {x['denominator']} | {x['shown']} | {x['picked']} | {x['got']} | "
                         f"{' · '.join(x['other_start']) or '-'}({x['other_start_shown']}) | {' · '.join(x['not_run']) or '-'} |")
            L.append(f"\n합(뜸) {c['sum_shown']}")
    else:
        L.append("걸음 기록 없음(v1 case3)")
    L += ["", "## 공정성 — 모델이 받은 허브 바이트(판마다 합) 중앙", "", "| 케이스/provider | 판 | 중앙 B |", "|---|---|---|"]
    for k, t in J["fairness"].items():
        L.append(f"| {k} | {t['runs']} | {t['median']} |")
    c = J["c_case23"]
    L += ["", "## C 비율 — 허브 응답에 인용이 있는데 실패", "",
          f"case2+3 실패 행 {c['fail_rows']} 중 **C {c['C']} = {pct(c['C'], c['fail_rows'])}** · invalid 제외 {J['invalid_excluded']}", "",
          "| 케이스/provider | 실패 행 | C | 비율 | 짧은 인용 | 대조 불가 |", "|---|---|---|---|---|---|"]
    for k, t in J["c_ratio"].items():
        L.append(f"| {k} | {t['fail_rows']} | {t['C']} | {pct(t['C'], t['fail_rows'])} | {t['short_quote']} | {t['not_comparable']} |")
    L += ["", "C 행:", "", "| 문항 | 케이스 | provider | 항목 | 원장 경로 |", "|---|---|---|---|---|"]
    for r in J["c_rows"]:
        L.append(f"| {r['no']} | {r['case']} | {r['provider']} | {ITEM.get(r['item'], r['item'])} | `{r['path']}` |")
    return "\n".join(L) + "\n"


async def run(runs_dir: Path, batch: str | None, run_ids: list[str] | None, *, rest: HubRest | None,
              raw: HubMCP | None, label: str) -> dict:
    runs = load_runs(runs_dir, batch, run_ids)
    if not runs:
        raise SystemExit(f"판 없음: {label}")
    quality = load_quality(runs_dir, {r["run_id"] for r in runs})
    hub_hash = None
    if rest is not None:
        try:
            hub_hash = ((await rest.health()).get("build") or {}).get("build_hash")
        except Exception:  # noqa: BLE001 — 허브 없음 = 전부 대조 불가
            hub_hash = None
    rp = Replay(rest if hub_hash else None, raw if hub_hash else None)
    acc = verify(runs, quality, rp, hub_hash)
    await rp.run()
    meta = {"label": label, "runs": len(runs), "hub_build_hash": hub_hash, "replayed": len(rp.calls),
            "truncation_markers_sha": truncation_markers_sha(), "markers": TRUNCATION_MARKERS,
            "bytes_slack": BYTES_SLACK}
    return finish(acc, rp, runs, meta)


def write(J: dict, out_dir: Path, name: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md, js = out_dir / f"verify-{name}.md", out_dir / f"verify-{name}.json"
    md.write_text(render(J), encoding="utf-8")
    full = {"meta": {**J["meta"], "ran_at": datetime.now(UTC).isoformat(timespec="seconds")},
            **{k: v for k, v in J.items() if k != "meta"}}
    js.write_text(json.dumps(full, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    return md, js


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="기록 검증(SPEC-006 v2 §4-4) — 판단 없이 셈")
    p.add_argument("--batch", help="배치 id(runs.jsonl batch_id)")
    p.add_argument("--runs-file", type=Path, help="run_id 목록 파일(한 줄 하나)")
    p.add_argument("--name", help="출력 이름(기본 배치 id)")
    p.add_argument("--out", type=Path, help="출력 폴더(기본 RUNS_DIR 옆 build-out)")
    p.add_argument("--no-hub", action="store_true", help="허브 재호출 안 함(전부 대조 불가)")
    a = p.parse_args(argv)
    if not a.batch and not a.runs_file:
        p.error("--batch 또는 --runs-file")
    s = get_settings()
    ids = [x.strip() for x in a.runs_file.read_text(encoding="utf-8").splitlines() if x.strip()] if a.runs_file else None
    name = a.name or a.batch or a.runs_file.stem
    out = (a.out or s.runs_path.parent / "build-out").resolve()
    if out.is_relative_to(ROOT):  # 공개 레포 — 기록에서 나온 출력은 레포 밖에만
        p.error(f"출력 폴더가 레포 안: --out 으로 레포 밖 폴더를 주세요({out.relative_to(ROOT)})")
    rest = None if a.no_hub else HubRest(s.hub_rest_url)
    raw = None if a.no_hub else HubMCP(s.hub_raw_mcp_url)
    J = asyncio.run(run(s.runs_path, a.batch, ids, rest=rest, raw=raw, label=name))
    md, js = write(J, out, name)
    t = J["truncation"]
    print(f"검증 {name} — 판 {J['meta']['runs']} · 잘림 " + " · ".join(f"{k} {v['truncated']}/{v['ok200']}" for k, v in t.items())
          + " · intent " + " · ".join(f"{k} {v['passed']}/{v['calls']}" for k, v in J["intent"].items())
          + f" · stray {sum(v['n'] for v in J['stray'].values())} · C {J['c_case23']['C']}/{J['c_case23']['fail_rows']}")
    print(f"→ {md.name} · {js.name}(출력 폴더 = 레포 밖)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
