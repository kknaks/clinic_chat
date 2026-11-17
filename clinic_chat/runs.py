"""기록 — `RUNS_DIR/runs.jsonl` 한 질문 한 줄(쓴 줄은 고치지 않는다) · 채점은 `quality.jsonl` 에 `run_id` 로 덧붙인다(SPEC-002 §7).

    uv run python -m clinic_chat.runs --show <run_id> [--column b --trace]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from clinic_chat.config import ROOT, load_settings

RUNS_FILE = "runs.jsonl"
QUALITY_FILE = "quality.jsonl"

# SPEC-002 §7-1 · §7-2 · §7-3 의 필드(시험 상수의 원본 — tests/test_runs.py 가 대조)
HEAD_FIELDS = ("run_id", "asked_at", "batch_id", "set_id", "question", "build", "commits", "models", "raw",
               "answer_key", "prompts", "settings", "order", "columns")
COLUMN_FIELDS = ("answer", "end_state", "timing", "tokens", "calls", "cost", "cited", "error", "trace")
B_ON_FIELDS = ("answer", "timing", "tokens", "calls", "cited", "answer_lines", "error")
B_TRACE_FIELDS = ("front", "entry", "entry_pick", "breadth", "steps", "visited", "verified_gids", "verified_lines",
                  "answer_lines", "gates", "llm_input_bytes", "llm_pages")
A_CALL_FIELDS = ("tool", "input", "output_bytes", "paths", "truncated")
C_CALL_FIELDS = ("tool", "input", "output_bytes", "gids", "edge_gold_ids")


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def git_commit(path: str | Path | None) -> str | None:
    if not path:
        return None
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def app_commit() -> str | None:
    return git_commit(ROOT)


class RunStore:
    def __init__(self, runs_dir: str | Path) -> None:
        self.dir = Path(runs_dir)

    def _append(self, name: str, row: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _read(self, name: str) -> list[dict]:
        p = self.dir / name
        if not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def append_run(self, row: dict) -> None:
        self._append(RUNS_FILE, row)

    def runs(self) -> list[dict]:
        return self._read(RUNS_FILE)

    def append_quality(self, row: dict) -> None:
        self._append(QUALITY_FILE, row)

    def quality(self) -> list[dict]:
        return self._read(QUALITY_FILE)

    def get(self, run_id: str) -> dict | None:
        return next((r for r in self.runs() if r.get("run_id") == run_id), None)


def show_view(row: dict, column: str | None = None, trace: bool = False) -> dict:
    """`runs --show` 이 찍는 내용(화면 서버 `GET /api/runs/{run_id}` 도 같은 함수)."""
    if column:
        col = (row.get("columns") or {}).get(column) or {}
        return col if trace else {k: v for k, v in col.items() if k != "trace"}
    view = {k: v for k, v in row.items() if k != "columns"}
    view["columns"] = {k: {x: y for x, y in (c or {}).items() if x in ("answer", "end_state", "error", "cited")}
                       for k, c in (row.get("columns") or {}).items()}
    return view


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.runs")
    ap.add_argument("--show", required=True)
    ap.add_argument("--column", choices=["a", "b", "b_on", "c", "b_full"])
    ap.add_argument("--trace", action="store_true")
    a = ap.parse_args()
    s = load_settings()
    row = RunStore(s.path(s.runs_dir)).get(a.show)
    if not row:
        raise SystemExit(f"run_id 없음: {a.show}")
    print(json.dumps(show_view(row, a.column, a.trace), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
