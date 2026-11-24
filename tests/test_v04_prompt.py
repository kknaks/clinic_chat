"""S8-T20 · E8-2 — 프롬프트 `pick` · 적재기(SPEC-008 §7 · WORK-017 WP-9)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from clinic_chat.consult.prompt import PromptError, load, parse

ROOT = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "prompts" / "consult.json"
CONV = Path(os.environ.get("INPUT_SPACE_CONV", "/nonexistent"))  # 입력 전수 표(프로필 레포 로컬 — 경로는 env 로만)


def _doc() -> dict:
    return json.loads(PROMPT.read_text(encoding="utf-8"))


def _p(d):
    return parse(json.dumps(d, ensure_ascii=False).encode())


def test_s8_t20_pick_loaded_and_ready():
    p = load(PROMPT)
    assert p.pick["version"] == "0.6.0-draft" and p.state_ready and len(p.pick["rules"]) >= 1  # 0.5 N1 · TP13
    assert set(p.pick["marks"]) == {"failed", "same_list", "none"}
    assert {"history", "object", "utterance"} <= set(p.pick["headers"])
    assert p.draft == p.version.endswith("-draft")  # draft = 최상위 version 만


@pytest.mark.parametrize("breaker", [
    lambda d: d.pop("pick"), lambda d: d["pick"].update(rules=[]), lambda d: d["pick"]["rules"].append(" "),
    lambda d: d["pick"]["headers"].pop("object"), lambda d: d["pick"]["marks"].update(failed=""),
    lambda d: d["pick"].update(version=""),
])
def test_s8_t20_pick_missing_or_blank_not_ready(breaker):
    d = _doc()
    breaker(d)
    p = _p(d)
    assert p.pick is None and not p.state_ready


def test_v05_topic_headers_optional():
    """0.5 TP13 · r7 P2-B — 주제 블록 머리 키(선택)가 없어도 `pick` 은 None 아님."""
    d = _doc()
    for k in ("topics", "topic_current", "topic_prev"):
        d["pick"]["headers"].pop(k, None)
    p = _p(d)
    assert p.pick is not None and p.state_ready


def test_s8_t20_old_keys_not_needed_and_jev_front_still_required():
    d = _doc()
    d.pop("jev_state")  # 절이 있으면 `retract` 는 꼴 검사(0.2) — 통째로 없는 꼴로 본다
    d["jev_front"].pop("target_option")
    assert _p(d).state_ready
    d2 = _doc()
    d2.pop("jev_front")
    with pytest.raises(PromptError, match="keys"):
        _p(d2)
    d3 = _doc()
    d3["version"] = "0.3.1"
    d3["pick"]["version"] = "0.4.0-draft"
    assert not _p(d3).draft


@pytest.mark.skipif(not CONV.is_file(), reason="입력 전수 표(프로필 레포 로컬)")
def test_e8_2_quoted_examples_not_in_table_turns():
    texts = [re.sub(r"\s+", "", t["text"]) for ln in CONV.read_text(encoding="utf-8").splitlines() if ln.strip()
             for t in json.loads(ln)["turns"]]
    quotes = [re.sub(r"\s+", "", q) for r in load(PROMPT).pick["rules"] for q in re.findall(r"「([^」]+)」", r)]
    assert quotes
    allowed = {"이해"}  # SPEC-008 §7 이 밝힌 예외(단계 이름 — 예문 아님)
    hit = [q for q in quotes if q not in allowed and any(q in t for t in texts)]
    assert hit == [], hit
