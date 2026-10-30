"""기록 — `RUNS_DIR/runs.jsonl` 한 질문 한 줄 · `human_checks.jsonl` 덧붙임(SPEC-006 §4-2). 기록 줄은 고치지 않는다."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from clinic_chat.config import ROOT

RUNS_FILE = "runs.jsonl"
CHECKS_FILE = "human_checks.jsonl"
QUALITY_FILE = "quality.jsonl"  # 「채점」 버튼 결과(①②) — 기록 줄은 고치지 않고 따로 덧붙임


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def app_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


class RunStore:
    def __init__(self, runs_dir: Path) -> None:
        self.dir = Path(runs_dir)

    def _append(self, name: str, row: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)  # 없으면 만듦
        with (self.dir / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _read(self, name: str) -> list[dict]:
        p = self.dir / name
        if not p.is_file():
            return []
        rows = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        return rows

    def append_run(self, record: dict) -> None:
        self._append(RUNS_FILE, record)

    def add_human_check(self, run_id: str, case: str, verdict: str, note: str = "", item: str | None = None) -> dict:
        if verdict not in ("맞음", "틀림"):
            raise ValueError("verdict 는 맞음 · 틀림")
        row = {"run_id": run_id, "case": case, "verdict": verdict, "item": item, "note": note, "at": now_iso()}
        self._append(CHECKS_FILE, row)
        return row

    def add_quality(self, entry: dict) -> None:
        self._append(QUALITY_FILE, entry)

    def latest_quality(self) -> dict[str, dict]:
        """run_id → {case: 가장 나중 채점} — 열마다 채점 줄(case 칸) · 옛 한꺼번에 채점 줄(cases 칸) 둘 다."""
        out: dict[str, dict] = {}
        for q in self._read(QUALITY_FILE):
            per = out.setdefault(q["run_id"], {})
            if q.get("case"):
                per[q["case"]] = q
            else:
                for c, v in (q.get("cases") or {}).items():
                    per[c] = {**v, "case": c, "at": q.get("at"), "cost_usd": None,
                              "_run_cost": q.get("cost_usd"), "judge_order": q.get("judge_order")}
        return out

    def get_raw(self, run_id: str) -> dict | None:
        for r in reversed(self._read(RUNS_FILE)):
            if r.get("run_id") == run_id:
                return r
        return None

    def get(self, run_id: str) -> dict | None:
        """기록 줄 + 나중 채점(quality.jsonl 마지막 것)을 겹친 보기."""
        r = self.get_raw(run_id)
        return merge_quality(r, self.latest_quality().get(run_id)) if r else None

    def checks(self) -> list[dict]:
        return self._read(CHECKS_FILE)

    def list(self, limit: int = 50) -> list[dict]:
        """기록 목록 — 시각 · 질문 · 열별 total_ms · 없는 이름 수 · ② 통과 수 · 사람 확인 · 골드 해시 앞 8자."""
        checks: dict[tuple[str, str], str] = {}
        for c in self.checks():
            checks[(c["run_id"], c["case"])] = c["verdict"]
        out = []
        qual = self.latest_quality()
        for r in reversed(self._read(RUNS_FILE)[-limit:]):
            r = merge_quality(r, qual.get(r["run_id"]))
            cases = {}
            for name, c in (r.get("cases") or {}).items():
                wc = c.get("word_check") or {}
                jd = c.get("judge") or {}
                cases[name] = {"total_ms": (c.get("timing") or {}).get("total_ms"), "error": c.get("error"),
                               "not_found": wc.get("not_found"), "judge_pass": jd.get("pass_count"),
                               "human": checks.get((r["run_id"], name)),
                               "cost_usd": ((r.get("cost") or {}).get("cases") or {}).get(name)}
            gh = (r.get("versions") or {}).get("gold_build_hash")
            v = r.get("versions") or {}
            out.append({"run_id": r["run_id"], "asked_at": r.get("asked_at"), "question": r.get("question"),
                        "provider": r.get("provider") or "sdk", "model": v.get("answer_model"),
                        "question_no": r.get("question_no"), "topic": r.get("topic"),
                        "effort": v.get("answer_effort"), "batch_id": r.get("batch_id"),
                        "gold": gh[:8] if gh else None, "cases": cases, "rerun_of": r.get("rerun_of"),
                        "cost": r.get("cost"), "graded": bool(r.get("graded_cases") or r.get("judge_order"))})
        return out


def merge_quality(rec: dict, per_case: dict | None) -> dict:
    """기록 줄 + 열마다 나중 채점(quality.jsonl)을 겹친 보기 — 기록 줄은 고치지 않는다."""
    if not per_case:
        return rec
    rec = {**rec, "cases": {c: dict(v) for c, v in (rec.get("cases") or {}).items()}}
    q_cost, run_costs, ats = 0.0, set(), []
    for c, v in per_case.items():
        if c in rec["cases"]:
            rec["cases"][c].update({k: v[k] for k in ("word_check", "judge") if k in v})
        if v.get("cost_usd") is not None:
            q_cost += v["cost_usd"]
        elif v.get("_run_cost") is not None:
            run_costs.add(v["_run_cost"])
        ats.append(v.get("at") or "")
    q_cost = round(q_cost + sum(run_costs), 6)
    cost = dict(rec.get("cost") or {})
    cost["quality_usd"] = q_cost
    cost["total_usd"] = round((cost.get("answers_usd") or 0.0) + q_cost, 6)
    rec["cost"] = cost
    rec["graded_cases"] = sorted(per_case)
    rec["quality_at"] = max(ats) if ats else None
    return rec
