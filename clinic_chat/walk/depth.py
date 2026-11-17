"""단계 ③ — Jev 깊이 판단(재귀 · SPEC-002 §3-1 단계 ③ · C1 ~ C3 · §3-4).

- 후보 Noul 은 32 묶음으로 **전부**. 결정 = `deepen` / `listed_only` / `uncertain` / `gate_forced` — **깊이만** 가른다.
  목록 · 답 재료(verified)에서 빼는 길이 없다.
- `answer_ready` 는 verified 를 갱신한 **뒤**(③-4) 묻는다. 그 값은 종료 제안일 뿐이다.
- C2: `uncertain` 은 required 가 미충족일 때만 noul 높은 순으로 한 걸음에 하나씩 깊이 본다 · 다 찼으면 `listed_only` 로 닫는다.
- C3: 걸음 상한(`WALK_MAX_STEPS`)을 넘으면 「실행 중단」. 읽기의 쪽 넘김 · 입구 고르기는 걸음이 아니다.
- 깊이 칸이 「없음」 인 범위(시술 — 0.5.0 부터 하위 층은 단계 ② · 부위·고민 · 동시 가능)는 ③-1 ~ ③-3 을 건너뛰고 ③-4 한 번.
- required = 읽기로 한 종류 가운데 yes · 1위 · 입구 지정(§3-2) — RQ1(`walk.required`)로 본다.
- Jev state 의 `verified` 는 식별 칸만(대상 · 표 · facet · 층 · 행 수 · `depth` — §3-6 「Jev state」).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clinic_chat import prompts
from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.clients.jev_client import Jev
from clinic_chat.config import Settings
from clinic_chat.walk.breadth import Ledger, Read, read_calls
from clinic_chat.walk.calls import Candidate, depth_calls, depth_candidates, depth_kind
from clinic_chat.walk.front import FrontResult, nonzero_rows
from clinic_chat.walk.jev_requests import ask_nouls, base_state
from clinic_chat.walk.kinds import ATTR_START, KIND_NAME
from clinic_chat.walk.ranges import range_name
from clinic_chat.walk.required import required_status
from clinic_chat.walk.verified import Verified


class StepLimit(RuntimeError):
    pass


def rq1(f: FrontResult, reads: list[Read]) -> dict:
    nodes = [e.node for e in f.entries if e.table != "offer_name"]
    return required_status(f.required, nodes, reads, f.coverage, applicable=f.applicable)


@dataclass
class DepthResult:
    steps: list[dict] = field(default_factory=list)
    decisions: dict[str, dict] = field(default_factory=dict)  # 후보 id → {decision, noul, closed_as, reason, range}
    answer_ready: float | None = None
    required: dict = field(default_factory=dict)
    exhausted: bool = False
    jev_requests: int = 0


async def walk_depth(question: str, hub: HubRest, jev: Jev, s: Settings, *, f: FrontResult, verified: Verified,
                     ledger: Ledger, reads: list[Read], build: dict | None,
                     coverage_of=None) -> DepthResult:
    out = DepthResult()
    entries = f.entries
    entry_nodes = {e.node for e in entries}
    cand_ranges = [ATTR_START] if f.attr_start else [r for r in f.ranges if depth_kind(r)]
    pending: dict[str, Candidate] = {}
    for r in cand_ranges:
        rows = [v.row for v in verified.rows.values() if r in v.ranges]
        for c in depth_candidates(r, rows, entries):
            pending.setdefault(c.id, c)
    cands: dict[str, Candidate] = {}
    read_cands: set[str] = set()
    tmpl = prompts.jev("candidate")
    ready_instr = prompts.jev("answer_ready")
    range_list = [{"code": r, "name": range_name(r)} for r in f.ranges]
    kind_list = [{"code": k, "name": KIND_NAME[k]} for k in f.kinds]

    def state() -> dict:
        req = rq1(f, reads)
        return base_state(question, build, ranges=range_list, kinds=kind_list, required=req["required"],
                          verified=verified.summary_for_jev(), unresolved=req["unresolved"])

    step = 0
    while True:
        step += 1
        if step > s.walk_max_steps:
            raise StepLimit(f"C3: 걸음 상한 {s.walk_max_steps} 넘음")
        rec: dict = {"step": step, "candidates": [], "batches": 0, "thresholds":
                     {"yes": s.jev_noul_yes_at, "no": s.jev_noul_no_at}}
        new = [c for c in pending.values() if c.id not in cands]
        pending = {}
        # ③-1 · ③-2 — 새 후보 전부 Noul(32 묶음) → 깊이 결정 · 후보 설명에 coverage 셈(§3-3)
        if new:
            if coverage_of is not None:
                cov = await coverage_of([c.id for c in new])
                for c in new:
                    c.rows = nonzero_rows(cov.get(c.id, {})) or None
            st = state()
            vals, _, n = await ask_nouls(jev, st, [c.as_state() for c in new], "candidate", tmpl, s.jev_batch_size)
            out.jev_requests += n
            rec["batches"] = n
            for i, (c, v) in enumerate(zip(new, vals, strict=True)):
                cands[c.id] = c
                if c.id in entry_nodes:
                    dec, why = "gate_forced", "G7 명시 대상"
                elif v >= s.jev_noul_yes_at:
                    dec, why = "deepen", "noul ≥ yes"
                elif v <= s.jev_noul_no_at:
                    dec, why = "listed_only", "noul ≤ no — 깊이 안 봄(목록에는 있음)"
                else:
                    dec, why = "uncertain", "yes 와 no 사이"
                out.decisions[c.id] = {"decision": dec, "noul": v, "reason": why, "range": c.range, "closed_as": None}
                rec["candidates"].append({"id": c.id, "relation": c.relation, "order": i, "noul": v, "decision": dec})
        req = rq1(f, reads)
        to_read = [cid for cid, d in out.decisions.items()
                   if d["decision"] in ("deepen", "gate_forced") and cid not in read_cands]
        open_unc = sorted((cid for cid, d in out.decisions.items() if d["decision"] == "uncertain"
                           and d["closed_as"] is None), key=lambda x: -out.decisions[x]["noul"])
        if open_unc and not req["ok"]:
            pick = open_unc[0]
            out.decisions[pick]["closed_as"] = "deepen"
            out.decisions[pick]["reason"] += " · C2 required 미충족 → 깊이"
            to_read.append(pick)
        elif open_unc:
            for cid in open_unc:
                out.decisions[cid]["closed_as"] = "listed_only"
                out.decisions[cid]["reason"] += " · C2 required 충족 → listed_only"
        # ③-3 — 깊이 읽기
        rec["read"] = []
        for cid in to_read:
            read_cands.add(cid)
            c = cands[cid]
            calls = depth_calls(c.range, c, f.kinds)
            new_reads = await read_calls(hub, calls, verified, ledger, reads, depth="deep",
                                         read_as_by_node=f.read_as_by_node(), follow=False)
            rec["read"].append({"id": cid, "calls": len(calls)})
            for r in new_reads:
                rows = [verified.rows[g].row for g in r.gids if g in verified.rows]
                for nc in depth_candidates(c.range, rows, entries):
                    if nc.id not in cands and nc.id not in pending:
                        pending[nc.id] = nc
        # ③-4 — 갱신된 verified 로 answer_ready(C1 — 한 걸음 안 여러 값이면 가장 낮은 값)
        _, extra, n = await ask_nouls(jev, state(), [], "candidate", tmpl, s.jev_batch_size,
                                      extra={"answer_ready": ready_instr})
        out.jev_requests += n
        ar = extra.get("answer_ready")
        rec["answer_ready"] = ar
        out.answer_ready = ar
        out.steps.append(rec)
        req = rq1(f, reads)
        more_unc = any(d["decision"] == "uncertain" and d["closed_as"] is None for d in out.decisions.values())
        if pending:
            continue
        if more_unc and not req["ok"]:
            continue
        if more_unc:  # required 충족 — 남은 uncertain 을 C2 로 닫는다(answer_ready 값과 무관)
            for d in out.decisions.values():
                if d["decision"] == "uncertain" and d["closed_as"] is None:
                    d["closed_as"] = "listed_only"
                    d["reason"] += " · C2 required 충족 → listed_only"
        break
    out.required = rq1(f, reads)
    out.exhausted = not out.required["ok"]
    return out
