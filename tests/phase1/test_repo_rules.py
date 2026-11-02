"""공개 레포 규칙(SPEC-006 §1 · §2-4) — 키 · 원장 · 시맨틱 · 실행 기록 · .env 가 git 추적 목록에 없음."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

RAW_FILES = [
    "anatomy-danger-zones", "combo-recipes", "complications", "consent-checklist", "contraindications",
    "extracted", "literature-evidence", "market-pricing", "patient-education", "pih-risk",
    "post-procedure-timeline", "pre-procedure-checklist", "problem-combos", "problem-packages",
    "procedure-comparison", "procedure-profiles", "products", "synergy", "taxonomy",
    "treatment-optimization", "treatment-sequence",
]
KEY_PATTERNS = [r"sk-ant-[A-Za-z0-9_\-]{8,}", r"sk-[A-Za-z0-9]{32,}", r"ts_[A-Za-z0-9]{24,}", r"tsk[-_][A-Za-z0-9]{16,}"]


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def test_raw_file_count_is_21():
    assert len(RAW_FILES) == 21


def test_no_secret_or_data_files_tracked():
    files = tracked()
    names = {Path(f).name for f in files}
    assert ".env" not in names
    assert not [f for f in files if f.endswith(".jsonl")]
    assert "semantic.json" not in names
    assert not [f for f in files if Path(f).name in {f"{n}.json" for n in RAW_FILES}]
    assert not [f for f in files if f.startswith(("runs/", ".cache/"))]


def test_no_key_like_strings_in_sources():
    pat = re.compile("|".join(KEY_PATTERNS))
    hits = []
    for f in tracked():
        p = ROOT / f
        if not p.is_file() or p.suffix in {".png", ".jpg", ".lock"}:
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if f == "tests/phase1/test_repo_rules.py":
            continue
        if pat.search(txt):
            hits.append(f)
    assert hits == []


def test_env_example_keys_blank_and_no_home_path():
    txt = (ROOT / ".env.example").read_text(encoding="utf-8")
    kv = dict(line.split("=", 1) for line in txt.splitlines() if "=" in line and not line.startswith("#"))
    assert kv["ANTHROPIC_API_KEY"].strip() == ""
    assert kv["TYPESAFE_API_KEY"].strip() == ""
    assert kv["RAW_DIR"].strip() == "" and kv["SEMANTIC_JSON"].strip() == ""
    assert "/Users/" not in txt


def test_no_home_path_in_tracked_sources():
    hits = [f for f in tracked() if (ROOT / f).is_file() and f != "tests/phase1/test_repo_rules.py"
            and "/Users/" in (ROOT / f).read_text(encoding="utf-8", errors="ignore")]
    assert hits == []
