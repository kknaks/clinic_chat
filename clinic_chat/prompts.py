"""프롬프트 파일 읽기 · sha256 · 사용자 확인 기록 대조(WORK-002 §5 「프롬프트 파일」 C-1 · §7).

- 줄 머리에는 SPEC-002 §7-1 의 `prompts{common, a, b, c, judge}` sha256 만 남긴다(칸을 더하지 않는다).
- 확인 기록 = `EVAL_SET_DIR/prompts-confirmed.json`(gitignore 로컬) `{files{<파일>: sha256}, by, at}`.
  하나라도 다르거나 기록이 없으면 「프롬프트 미확정(사용자 확인 전)」.

    uv run python -m clinic_chat.prompts --confirm --by <확인한 사람>
    uv run python -m clinic_chat.prompts --status
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from clinic_chat.config import ROOT, Settings, load_settings

PROMPT_DIR = ROOT / "prompts"
HEAD_FILES = {"common": "common.md", "a": "col_a.md", "b": "col_b.md", "c": "col_c.md", "judge": "judge.md"}
JEV_FILES = ("front.md", "entry.md", "candidate.md", "answer_ready.md", "value.md")
UNCONFIRMED_MARK = "프롬프트 미확정(사용자 확인 전)"
CONFIRM_FILE = "prompts-confirmed.json"


def read(name: str, base: Path | None = None) -> str:
    return ((base or PROMPT_DIR) / name).read_text(encoding="utf-8")


def jev(name: str, base: Path | None = None) -> str:
    """Jev instructions(영어 — D10). 파일 안 `<!-- … -->` 주석 줄은 뺀다."""
    text = read(f"jev/{name}.md", base)
    return "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("<!--")).strip()


def jev_sections(name: str, base: Path | None = None) -> dict[str, str]:
    """`## <이름>` 절로 나뉜 Jev instructions 파일(예: `front.md` — target · kind · gap)."""
    out: dict[str, list[str]] = {}
    cur: str | None = None
    for ln in jev(name, base).splitlines():
        if ln.startswith("## "):
            cur = ln[3:].strip()
            out[cur] = []
        elif cur is not None:
            out[cur].append(ln)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def all_files(base: Path | None = None) -> list[str]:
    return [*HEAD_FILES.values(), *(f"jev/{f}" for f in JEV_FILES)]


def file_hashes(base: Path | None = None) -> dict[str, str]:
    b = base or PROMPT_DIR
    return {f: hashlib.sha256((b / f).read_bytes()).hexdigest() for f in all_files(b) if (b / f).is_file()}


def head_hashes(base: Path | None = None) -> dict[str, str]:
    """줄 머리 `prompts{common, a, b, c, judge}`."""
    h = file_hashes(base)
    return {k: h.get(f) for k, f in HEAD_FILES.items()}


def confirm_path(settings: Settings) -> Path:
    p = settings.path(settings.eval_set_dir)
    assert p is not None
    return p / CONFIRM_FILE


def status(settings: Settings, base: Path | None = None) -> dict:
    """{confirmed: bool, mark: str | None, differ: [파일]} — 기록이 없거나 하나라도 다르면 미확정."""
    cur = file_hashes(base)
    p = confirm_path(settings)
    if not p.is_file():
        return {"confirmed": False, "mark": UNCONFIRMED_MARK, "differ": sorted(cur), "record": None}
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {"confirmed": False, "mark": UNCONFIRMED_MARK, "differ": sorted(cur), "record": None}
    saved = rec.get("files") or {}
    differ = sorted(f for f in set(cur) | set(saved) if cur.get(f) != saved.get(f))
    ok = not differ
    return {"confirmed": ok, "mark": None if ok else UNCONFIRMED_MARK, "differ": differ,
            "record": {"by": rec.get("by"), "at": rec.get("at")}}


def confirm(settings: Settings, by: str, base: Path | None = None) -> dict:
    p = confirm_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"files": file_hashes(base), "by": by, "at": datetime.now(UTC).astimezone().isoformat(timespec="seconds")}
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.prompts")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--by")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    s = load_settings()
    if a.confirm:
        if not a.by:
            ap.error("--confirm 에는 --by <확인한 사람> 이 필요하다")
        rec = confirm(s, a.by)
        print(json.dumps({"ok": True, "files": len(rec["files"]), "by": rec["by"], "at": rec["at"]}, ensure_ascii=False))
        return
    print(json.dumps(status(s), ensure_ascii=False))


if __name__ == "__main__":
    main()
