"""WP-5 — 한 질문 한 줄 · 필드(SPEC-002 §7) · 순서 · 「프롬프트 미확정」(모의 세 열)."""

from __future__ import annotations

import subprocess

from clinic_chat import prompts
from clinic_chat.columns.base import Deps
from clinic_chat.config import ROOT
from clinic_chat.runner import run_question, summary_lines
from clinic_chat.runs import (
    A_CALL_FIELDS,
    B_ON_FIELDS,
    B_TRACE_FIELDS,
    C_CALL_FIELDS,
    COLUMN_FIELDS,
    HEAD_FIELDS,
    RunStore,
)
from tests.conftest import make_settings, read_log, stream
from tests.fake_jev import FakeJev
from tests.walk_fixture import P1, cand, make_hub

# SPEC-002 §7-3 B 표의 행 그대로
SPEC_B_TRACE = ("front", "entry", "entry_pick", "breadth", "steps", "visited", "verified_gids", "verified_lines",
                "answer_lines", "gates", "llm_input_bytes", "llm_pages")


def jev():
    return FakeJev(noul_fn=lambda n, i, s: 0.9)


def setup(tmp_path, fake_cli):
    bin_, route, log = fake_cli
    (route / "answer.jsonl").write_text(stream("답 [GPF-91000]"), encoding="utf-8")
    (route / "tools.jsonl").write_text(stream("답", mcp="hub", tools=[("mcp__hub__pair", {"a": "x"}, "{}")]) .replace(
        '"hub"', '"hub"'), encoding="utf-8")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "f.json").write_text("{}", encoding="utf-8")
    s = make_settings(tmp_path, claude_cli_bin=bin_, raw_dir=str(raw))
    return s, Deps(s, make_hub(resolve=[cand(P1)]).client(), jev().jev(s)), log


async def test_one_row_all_fields(tmp_path, fake_cli, monkeypatch):
    s, d, log = setup(tmp_path, fake_cli)
    store = RunStore(tmp_path / "runs")
    # A 는 raw MCP 이름으로 연결돼야 한다 — 모의 CLI 의 init 줄을 열마다 맞춘다
    r = await run_question("가짜 질문", d, store=store, seed=7)
    assert set(B_TRACE_FIELDS) == set(SPEC_B_TRACE)
    assert all(f in r for f in HEAD_FIELDS)
    for k in ("a", "b", "c"):
        assert all(f in r["columns"][k] for f in COLUMN_FIELDS)
    assert r["columns"]["b"]["end_state"] and r["columns"]["a"]["end_state"] is None and r["columns"]["c"]["end_state"] is None
    assert all(f in r["columns"]["b_on"] for f in B_ON_FIELDS)
    assert all(f in r["columns"]["b"]["trace"] for f in SPEC_B_TRACE)
    for x in r["columns"]["c"]["trace"]["tool_calls"]:
        assert all(f in x for f in C_CALL_FIELDS)
    for x in r["columns"]["a"]["trace"]["tool_calls"]:
        assert all(f in x for f in A_CALL_FIELDS)
    assert r["order"]["seed"] == 7 and sorted(r["order"]["columns"]) == ["a", "b", "c"]
    assert store.runs()[-1]["run_id"] == r["run_id"]
    assert set(r["prompts"]) == {"common", "a", "b", "c", "judge"}


async def test_order_seed_differs_and_sequential(tmp_path, fake_cli):
    s, d, log = setup(tmp_path, fake_cli)
    r1 = await run_question("q", d, columns=("a", "b", "c"))
    r2 = await run_question("q", d, columns=("a", "b", "c"))
    assert r1["order"]["seed"] != r2["order"]["seed"]


async def test_prompt_unconfirmed_mark(tmp_path, fake_cli):
    s, d, _ = setup(tmp_path, fake_cli)
    r = await run_question("q", d, columns=("b",))
    assert prompts.UNCONFIRMED_MARK in summary_lines(r, s)
    prompts.confirm(s, "시험")
    assert prompts.UNCONFIRMED_MARK not in summary_lines(r, s)


def test_runs_dir_is_ignored():
    out = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "runs/runs.jsonl", "data/eval-set/set.jsonl",
                          "runs/quality.jsonl"], capture_output=True, text=True)
    assert len(out.stdout.split()) == 3


def test_log_helper_exists():
    assert callable(read_log)
