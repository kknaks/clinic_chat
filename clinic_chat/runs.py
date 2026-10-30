"""기록 — `RUNS_DIR/runs.jsonl` 한 질문 한 줄 · `human_checks.jsonl` 덧붙임(SPEC-006 §4-2). 기록 줄은 고치지 않는다."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from clinic_chat.config import ROOT

RUNS_FILE = "runs.jsonl"
CHECKS_FILE = "human_checks.jsonl"


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

    def get(self, run_id: str) -> dict | None:
        for r in reversed(self._read(RUNS_FILE)):
            if r.get("run_id") == run_id:
                return r
        return None

    def checks(self) -> list[dict]:
        return self._read(CHECKS_FILE)

    def list(self, limit: int = 50) -> list[dict]:
        """기록 목록 — 시각 · 질문 · 열별 total_ms · 없는 이름 수 · ② 통과 수 · 사람 확인 · 골드 해시 앞 8자."""
        checks: dict[tuple[str, str], str] = {}
        for c in self.checks():
            checks[(c["run_id"], c["case"])] = c["verdict"]
        out = []
        for r in reversed(self._read(RUNS_FILE)[-limit:]):
            cases = {}
            for name, c in (r.get("cases") or {}).items():
                wc = c.get("word_check") or {}
                jd = c.get("judge") or {}
                cases[name] = {"total_ms": (c.get("timing") or {}).get("total_ms"), "error": c.get("error"),
                               "not_found": wc.get("not_found"), "judge_pass": jd.get("pass_count"),
                               "human": checks.get((r["run_id"], name))}
            gh = (r.get("versions") or {}).get("gold_build_hash")
            out.append({"run_id": r["run_id"], "asked_at": r.get("asked_at"), "question": r.get("question"),
                        "gold": gh[:8] if gh else None, "cases": cases, "rerun_of": r.get("rerun_of")})
        return out
