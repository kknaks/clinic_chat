"""일괄 실행(배치) — 질문 리스트 × provider 를 순차로(cli-research §6-4 · 10-30 사용자: 만들되 실행은 안 함).

- 계획 파일 `RUNS_DIR/batches/<batch_id>.json`(질문 × provider 칸 · 상태 todo|running|done|failed) · 끝난 판은 runs.jsonl 한 줄
  (`batch_id` · `batch_item` 칸 더함)
- 순서: 질문 → provider 번갈아(Q1 claude · Q1 codex · Q2 claude …) — 한 provider 가 한도에 걸려도 다른 쪽은 계속
- 판 사이 간격 · 재시도 수 · 재시도 간격 · 한도 대기 = 설정(BATCH_*) — **기본값 비움 · 비면 시작하지 않음**(사용자가 정함)
- 재시도는 기동 실패 · 비정상 종료 · 시간 초과 · 과부하만. 답했는데 나쁜 것 · 거절 · 스키마 미달은 그대로 기록(실험 결과)
- 한도: Claude `rate_limit_event`(rejected) · Codex 「usage limit」 문구 → 그 provider 를 resetsAt(모르면 BATCH_LIMIT_WAIT_SEC)까지 멈춤 ·
  둘 다 막히면 배치가 그때까지 잠듦
- 끊겨도 이어서: 다시 띄우면 done 아닌 칸부터 · running 으로 남은 칸은 runs.jsonl 에 결과가 있으면 done

    uv run python -m clinic_chat.batch --providers claude-cli,codex-cli [--questions 1,3,4] [--resume <batch_id>] [--plan-only]
        [--cases case3](걷기 고치는 동안 · case3 만 · 2회는 같은 판으로 --label …-r1 · …-r2)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from clinic_chat.config import Settings
from clinic_chat.llm import PROVIDERS
from clinic_chat.runs import RunStore, now_iso

RETRYABLE = re.compile(r"시간 초과|종료 코드|기동|overloaded|과부하|MCP 연결 안 됨|ConnectError|Timeout", re.IGNORECASE)
Executor = Callable[[str, str, dict], Awaitable[dict]]  # (질문, provider, 기록에 더할 칸) → 기록 줄


class BatchConfigError(RuntimeError):
    pass


def parse_questions(md: str) -> list[dict]:
    """questions.md 표 줄 `| 번호 | 질문 | …` → [{n, text}]."""
    out = []
    for line in md.splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            out.append({"n": int(m.group(1)), "text": m.group(2).strip()})
    return out


META = ("question_no", "src_id", "topic", "reference_answer", "source_kind", "group")


def parse_question_set(data: dict) -> list[dict]:
    """question-set.json → [{n, text, meta}] — meta 는 기록(runs · quality)에 그대로 남김. 참고 답은 채점에 넣지 않음."""
    out = []
    for q in data.get("questions", []):
        meta = {"question_no": q["no"], "src_id": q.get("src_id"), "topic": q.get("topic"),
                "reference_answer": q.get("reference_answer"), "source_kind": q.get("source_kind"), "group": q.get("group")}
        out.append({"n": q["no"], "text": q["question"], "meta": meta})
    return out


def load_questions(s: Settings) -> list[dict]:
    if s.question_set_json:
        if not Path(s.question_set_json).is_file():
            raise BatchConfigError("QUESTION_SET_JSON 파일 없음")
        return parse_question_set(json.loads(Path(s.question_set_json).read_text(encoding="utf-8")))
    if not s.questions_md or not Path(s.questions_md).is_file():
        raise BatchConfigError("QUESTION_SET_JSON · QUESTIONS_MD 없음(질문 파일)")
    return parse_questions(Path(s.questions_md).read_text(encoding="utf-8"))


def parse_pick(spec: str) -> list | None:
    """「Q001-Q003,Q010」 · 「1,3,4」 → 번호 목록(문자 번호는 범위 펼침)."""
    out: list = []
    for part in [x.strip() for x in spec.split(",") if x.strip()]:
        m = re.match(r"^([A-Za-z]*)(\d+)-([A-Za-z]*)(\d+)$", part)
        if m:
            pre, a, b = m.group(1), int(m.group(2)), int(m.group(4))
            width = len(m.group(2))
            out += [f"{pre}{i:0{width}d}" if pre else i for i in range(a, b + 1)]
        else:
            out.append(int(part) if part.isdigit() else part)
    return out or None


def config_problems(s: Settings) -> list[str]:
    """배치 시작 전에 사용자가 정해야 하는 값 — 비면 시작하지 않음."""
    bad = []
    if s.batch_gap_sec is None:
        bad.append("BATCH_GAP_SEC")
    if s.batch_retries is None:
        bad.append("BATCH_RETRIES")
    elif s.batch_retries > 0 and len(backoffs(s)) < s.batch_retries:
        bad.append(f"BATCH_RETRY_BACKOFF_SEC(재시도 {s.batch_retries}번 만큼)")
    if s.batch_limit_wait_sec is None:
        bad.append("BATCH_LIMIT_WAIT_SEC")
    return bad


def backoffs(s: Settings) -> list[float]:
    return [float(x) for x in re.split(r"[,\s]+", s.batch_retry_backoff_sec.strip()) if x]


class BatchStore:
    def __init__(self, runs_dir: Path) -> None:
        self.dir = Path(runs_dir) / "batches"

    def path(self, batch_id: str) -> Path:
        return self.dir / f"{batch_id}.json"

    def save(self, plan: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        plan["updated_at"] = now_iso()
        tmp = self.path(plan["batch_id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path(plan["batch_id"]))

    def load(self, batch_id: str) -> dict:
        return json.loads(self.path(batch_id).read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        out = []
        for p in sorted(self.dir.glob("*.json"), reverse=True):
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({k: d.get(k) for k in ("batch_id", "created_at", "updated_at", "state", "providers", "progress")})
        return out


def make_plan(questions: list[dict], providers: list[str], pick: list | None = None, skip: set | None = None,
              label: str | None = None) -> dict:
    """skip = 이미 끝난 번호(재개 때 건너뜀)."""
    for p in providers:
        if p not in PROVIDERS:
            raise BatchConfigError(f"모르는 provider: {p}")
    qs = [q for q in questions if (not pick or q["n"] in pick) and not (skip and q["n"] in skip)]
    items = [{"key": f"{q['n']}:{p}", "n": q["n"], "question": q["text"], "provider": p, "status": "todo",
              "attempts": 0, "run_id": None, "error": None, "meta": q.get("meta") or {}} for q in qs for p in providers]
    plan = {"batch_id": f"b{time.strftime('%Y%m%d%H%M')}-{uuid.uuid4().hex[:6]}", "created_at": now_iso(),
            "label": label, "providers": providers, "questions": [q["n"] for q in qs], "state": "planned", "items": items,
            "paused": {}, "log": []}
    progress(plan)
    return plan


def progress(plan: dict) -> dict:
    st = [i["status"] for i in plan["items"]]
    plan["progress"] = {"total": len(st), "done": st.count("done"), "failed": st.count("failed"),
                        "todo": st.count("todo"), "running": st.count("running")}
    return plan["progress"]


def outcome(rec: dict) -> tuple[str, dict]:
    """판 결과 → done | retry | limited (+ 정보)."""
    cases = {k: c for k, c in (rec.get("cases") or {}).items() if not (c or {}).get("skipped")}  # 안 돌린 열 빼고
    clis = [(c.get("trace") or {}).get("cli") or {} for c in cases.values()]
    if any(x.get("rate_limited") for x in clis):
        resets = [rl.get("resetsAt") for x in clis for rl in x.get("rate_limits") or [] if rl.get("resetsAt")]
        return "limited", {"resets_at": max(resets) if resets else None}
    errors = [c.get("error") for c in cases.values() if c.get("error")]
    if errors and len(errors) == len(cases) and all(RETRYABLE.search(e) for e in errors):
        return "retry", {"errors": errors}
    return "done", {}


def resume_fix(plan: dict, store: RunStore) -> None:
    """running 으로 남은 칸 — runs.jsonl 에 결과가 있으면 done · 없으면 다시."""
    for it in plan["items"]:
        if it["status"] == "running":
            it["status"] = "done" if it.get("run_id") and store.get_raw(it["run_id"]) else "todo"


async def run_batch(plan: dict, *, settings: Settings, bstore: BatchStore, execute: Executor,
                    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, now: Callable[[], float] = time.time,
                    should_stop: Callable[[], bool] = lambda: False, run_store: RunStore | None = None,
                    report: Callable[[dict], None] | None = None, report_every: float = 1800) -> dict:
    """report — report_every 초마다 진행 한 줄(진행 수 · 오류 수 · 지금 번호)."""
    bad = config_problems(settings)
    if bad:
        raise BatchConfigError("일괄 실행 설정을 먼저 정하세요: " + " · ".join(bad))
    if run_store:
        resume_fix(plan, run_store)
    gap, retries, waits, limit_wait = settings.batch_gap_sec, settings.batch_retries, backoffs(settings), \
        settings.batch_limit_wait_sec
    plan["state"] = "running"
    bstore.save(plan)
    first = True
    last_report = now()
    while True:
        if report and now() - last_report >= report_every:
            last_report = now()
            report(summary(plan))
        todo = [i for i in plan["items"] if i["status"] == "todo"]
        if not todo or should_stop():
            break
        ready = [i for i in todo if plan["paused"].get(i["provider"], 0) <= now()]
        if not ready:  # 남은 provider 가 다 한도에 걸림 — 가장 빨리 열리는 때까지 잠듦
            until = min(plan["paused"][i["provider"]] for i in todo)
            plan["state"] = "sleeping"
            plan["log"].append({"at": now_iso(), "sleep_until": until})
            bstore.save(plan)
            await sleep(max(0.0, until - now()))
            plan["state"] = "running"
            continue
        it = ready[0]
        if not first and gap:
            await sleep(gap)
        first = False
        it["status"], it["attempts"] = "running", it["attempts"] + 1
        bstore.save(plan)
        try:
            rec = await execute(it["question"], it["provider"],
                                {"batch_id": plan["batch_id"], "batch_item": it["key"], "batch_label": plan.get("label"),
                                 **(it.get("meta") or {})})
            kind, info = outcome(rec)
            it["run_id"] = rec.get("run_id")
        except Exception as e:  # noqa: BLE001 — 판 하나 실패는 재시도 판정으로
            kind, info = "retry", {"errors": [f"{type(e).__name__}: {e}"[:300]]}
        if kind == "done":
            it["status"], it["error"] = "done", None
        elif kind == "limited":
            it["status"], it["attempts"] = "todo", it["attempts"] - 1  # 한도는 재시도로 안 셈
            until = info["resets_at"] if info.get("resets_at") else now() + limit_wait
            plan["paused"][it["provider"]] = float(until)
            plan["log"].append({"at": now_iso(), "provider": it["provider"], "limited_until": until})
        elif it["attempts"] <= retries:
            it["status"], it["error"] = "todo", "; ".join(info.get("errors", []))[:300]
            bstore.save(plan)
            await sleep(waits[it["attempts"] - 1] if it["attempts"] - 1 < len(waits) else (waits[-1] if waits else 0))
        else:
            it["status"], it["error"] = "failed", "; ".join(info.get("errors", []))[:300]
        progress(plan)
        bstore.save(plan)
    plan["state"] = "stopped" if should_stop() and any(i["status"] == "todo" for i in plan["items"]) else "done"
    progress(plan)
    bstore.save(plan)
    return plan


def summary(plan: dict) -> dict[str, Any]:
    cur = next((i for i in plan["items"] if i["status"] == "running"), None)
    return {"batch_id": plan["batch_id"], "state": plan["state"], "progress": plan["progress"],
            "now": cur and {"n": cur["n"], "provider": cur["provider"]}, "paused": plan.get("paused"),
            "failed": [i["key"] for i in plan["items"] if i["status"] == "failed"]}


async def _main() -> None:
    from clinic_chat import runner
    from clinic_chat.config import get_settings
    from clinic_chat.runs import app_commit
    from clinic_chat.server import build_deps, startup_check

    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="claude-cli")
    ap.add_argument("--questions", default="", help="예 Q001-Q003 · 비면 전체")
    ap.add_argument("--questions-file", help="번호 목록 파일(한 줄에 하나 · 예 rerun-questions.txt)")
    ap.add_argument("--label", help="일괄 라벨(기록 batch_label · 예 rerun-v3)")
    ap.add_argument("--resume")
    ap.add_argument("--skip-done", action="store_true", help="runs.jsonl 에 이미 있는 번호는 건너뜀")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--cases", default="", help="돌릴 케이스(예 case3 · 비면 셋 다) — 안 돌린 열은 skipped 빈 열 · 채점 안 함")
    a = ap.parse_args()
    s = get_settings()
    run_cases = tuple(c.strip() for c in a.cases.split(",") if c.strip()) or None
    if run_cases and any(c not in runner.CASES for c in run_cases):
        raise SystemExit(f"[batch] 모르는 케이스: {run_cases}")
    bstore, rstore = BatchStore(s.runs_path), RunStore(s.runs_path)
    if a.resume:
        plan = bstore.load(a.resume)
    else:
        skip = None
        if a.skip_done:  # 끝난 번호 = 답 기록 + 열마다 채점이 있는 것
            skip = {r.get("question_no") for r in rstore._read("runs.jsonl") if r.get("question_no")
                    and any((c or {}).get("judge") for c in (r.get("cases") or {}).values())}
        pick = parse_pick(a.questions)
        if a.questions_file:
            pick = [x.strip() for x in Path(a.questions_file).read_text(encoding="utf-8").splitlines() if x.strip()]
        if a.skip_done and a.label:  # 같은 라벨에서 끝난 번호만 건너뜀(다른 판 기록은 그대로 둠)
            skip = {r.get("question_no") for r in rstore._read("runs.jsonl") if r.get("batch_label") == a.label
                    and any((c or {}).get("judge") for c in (r.get("cases") or {}).values())}
        plan = make_plan(load_questions(s), [p.strip() for p in a.providers.split(",") if p.strip()], pick, skip,
                         label=a.label)
        if run_cases:
            plan["cases"] = list(run_cases)
        bstore.save(plan)
    run_cases = tuple(plan.get("cases") or ()) or None  # 이어 돌릴 때도 계획 파일의 케이스 그대로
    print(f"[batch] {plan['batch_id']} · 라벨 {plan.get('label')} · 케이스 {plan.get('cases') or '전부'} · {plan['progress']}",
          flush=True)
    if a.plan_only:
        return
    deps = build_deps(s)
    deps.app_commit = app_commit()
    from clinic_chat.llm import cli

    cli.MAX_CONCURRENT = max(1, s.cli_max_concurrent)
    st = await startup_check(deps)
    print(f"[batch] 기동 · 허브 {st.get('hub_health')} · 골드 MCP {st.get('gold_mcp')} · 원장 MCP {st.get('raw_mcp')} · "
          f"intent 도구 {st.get('intent_tools')}", flush=True)
    if not isinstance(st.get("gold_mcp"), int) or not isinstance(st.get("raw_mcp"), int):
        raise SystemExit("[batch] 허브(18005) 연결 안 됨 — 멈춤")

    async def execute(q: str, provider: str, extra: dict) -> dict:
        # 답 세 열 + 열마다 ①② 채점이 모두 끝난 뒤 돌아옴(다음 문항은 그 뒤)
        rec = await runner.execute(deps, q, lambda e, d: None, provider=provider,
                                   quality=runner.auto_quality(s, provider), cases=run_cases)
        rec.update(extra)
        rstore.append_run(rec)
        meta = {k: extra.get(k) for k in META if k in extra}
        for c in runner.gradable(rec["cases"]):
            cr = rec["cases"][c]
            if cr.get("judge"):
                rstore.add_quality({"run_id": rec["run_id"], "case": c, "at": now_iso(), "mode": "absolute",
                                    "judge_provider": s.judge_provider, "word_check": cr.get("word_check"),
                                    "judge": cr.get("judge"), "cost_usd": None, **meta})
        errs = sum(1 for cr in rec["cases"].values() if cr.get("error"))
        print(f"[batch] {extra.get('question_no') or extra.get('batch_item')} 끝 · 오류 열 {errs} · "
              f"통과 {[ (rec['cases'][c].get('judge') or {}).get('pass_count') for c in runner.CASES]}", flush=True)
        return rec

    def report(sm: dict) -> None:
        p = sm["progress"]
        print(f"[batch] {time.strftime('%H:%M')} 진행 {p['done']}/{p['total']} · 실패 {p['failed']} · "
              f"지금 {sm.get('now')} · 한도 멈춤 {sm.get('paused') or '-'}", flush=True)

    plan = await run_batch(plan, settings=s, bstore=bstore, execute=execute, run_store=rstore, report=report)
    print(f"[batch] 끝 · {plan['state']} · {plan['progress']}", flush=True)


if __name__ == "__main__":
    asyncio.run(_main())
