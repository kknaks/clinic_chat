"""S8-T2 · S8-T3 — 이해 호출 인자 · 턴 메시지 · 실패(SPEC-008 UC1 ~ UC10 · FL1 · FL4 · WORK-017 WP-2). 가짜 CLI."""

from __future__ import annotations

import asyncio
from pathlib import Path

from clinic_chat.consult import entry_list as el_mod
from clinic_chat.consult import prompt as prompt_mod
from clinic_chat.consult import state as cst
from clinic_chat.consult import understand as und
from clinic_chat.llm.chat_cli import ChatCliSpec, chat_args
from clinic_chat.llm.cli import DISALLOWED_TOOLS
from tests.conftest import make_settings, read_log
from tests.v04_fixture import entry_rows, set_out

PICK = prompt_mod.load(Path(__file__).resolve().parent.parent / "prompts" / "consult.json").pick
EL = el_mod.build(entry_rows(), "fakehash", aliases=True)


def _s(tmp_path, **kw):
    return make_settings(tmp_path, chat_cli_cwd=str(tmp_path / "cwd"), **kw)


def test_s8_t2_args_isolation_and_no_session(tmp_path):
    s = _s(tmp_path)
    a = und.args(s, "SYS")
    assert "--no-session-persistence" in a
    for f in ("--resume", "--session-id", "--fallback-model"):
        assert f not in a
    assert a[a.index("--model") + 1] == "sonnet" and a[a.index("--effort") + 1] == "low"
    assert a[a.index("--max-turns") + 1] == "1" and a[a.index("--output-format") + 1] == "json"
    assert a[a.index("--system-prompt") + 1] == "SYS"
    ca = chat_args(ChatCliSpec.from_settings(s), "sid", "new", "X")  # 격리 인자 = 답 실행기와 같은 값
    for f in ("--setting-sources", "--tools", "--disallowedTools", "--permission-mode"):
        assert a[a.index(f) + 1] == ca[ca.index(f) + 1]
    assert a[a.index("--disallowedTools") + 1] == DISALLOWED_TOOLS
    for f in ("--disable-slash-commands", "--strict-mcp-config"):
        assert f in a and f in ca
    assert a[a.index("--settings") + 1] == '{"alwaysThinkingEnabled": false}'  # E8-0 ⓐ
    assert und.env(s)["MAX_THINKING_TOKENS"] == "0"
    assert "--settings" not in und.args(_s(tmp_path, chat_pick_thinking="on"), "S")


def test_s8_t2_system_text_is_rules_plus_list():
    t = und.system_text(PICK, EL)
    assert t == "\n".join(PICK["rules"]) + "\n\n" + EL.text
    assert t.startswith(PICK["rules"][0]) and "## 고민 (table = concern)" in t


def _row(n, q, targets, kind="new", *, ask=None, answer="", lst=None, err=None, uerr=None):
    acc = {"targets": [{"table": t, "code": c, "name": nm, "sure": True} for t, c, nm in targets], "kind": kind}
    return {"turn": n, "question": q, "error": err, "answer": answer, "list": lst,
            "consult": {"understand": {"accepted": acc, "error": uerr}, "turn_kind": {"kind": kind}, "ask": ask}}


def test_s8_t3_turn_message_blocks(tmp_path):
    s = _s(tmp_path, chat_pick_context_turns=2, chat_pick_answer_chars=5, chat_pick_list_max=2)
    lst = {"interest": [], "concerns": [{"modalities": [{"modality_code": "ZZM-1", "modality_name": "가짜계열",
                                                          "procedures": [{"procedure_code": "ZZP-1", "procedure_name": "가짜시술"}]},
                                                         {"modality_code": "ZZM-2", "modality_name": "가짜계열둘", "procedures": []}]}],
           "synergy": []}
    ask = {"slot": "concern", "about": "site:ZZS-1",
           "options": [{"table": "concern", "code": "ZZC-1", "name": "가짜고민"}]}
    turns = [_row(1, "첫말", [("site", "ZZS-1", "가짜부위")], ask=ask, answer="첫답글자열개쯤", lst=None),
             _row(2, "둘말", [("concern", "ZZC-1", "가짜고민")], answer="둘째답이에요", lst=lst),
             _row(3, "셋말", [], kind="talk", answer="셋째답", lst=None),
             _row(4, "넷말", [], uerr={"kind": "timeout"})]
    obj = cst.empty()
    obj["concerns"].append({"table": "concern", "code": "ZZC-1", "name": "가짜고민", "state": "said"})
    obj["sites"].append({"table": "site", "code": "ZZS-1", "name": "가짜부위", "state": "guess"})
    obj["subjects"].append({"table": "procedure", "code": "ZZP-1", "name": "가짜시술", "state": "withdrawn"})
    m = und.turn_message(turns, obj, "이번말", PICK, s, EL)
    L = m.splitlines()
    h, mk = PICK["headers"], PICK["marks"]
    assert L[0] == h["history"] and L[1] == f"1. {h['user']} 첫말"
    assert L[2] == f"   {h['picked']} 가짜부위(site:ZZS-1) · new"
    assert L[3] == f"   {h['options']} 1) 가짜고민(concern:ZZC-1)"
    assert not any(h["answer"] in x and "첫답" in x for x in L)  # 가까운 2 턴 밖 — 답 요약 없음
    assert f"   {h['answer']} 둘째답이에" in L  # 앞 5 자
    assert f"   {h['list']} [가짜계열(modality:ZZM-1)] 1) 가짜시술(procedure:ZZP-1) [가짜계열둘(modality:ZZM-2)]" in L  # 번호 = 시술 줄만
    assert f"   {h['picked']} {mk['none']} · talk" in L and f"   {h['list']} {mk['same_list']}" in L
    assert f"4. {h['user']} 넷말 {mk['failed']}" in L  # FL4
    i = L.index(h["object"])
    assert L[i + 1] == f"{h['obj_targets']} 가짜고민(concern:ZZC-1)"
    assert L[i + 2] == f"{h['obj_guess']} 가짜부위(site:ZZS-1)"
    assert L[i + 3] == f"{h['obj_withdrawn']} 가짜시술(procedure:ZZP-1)"
    assert L[-2:] == [h["utterance"], "이번말"]


def test_s8_t3_first_turn_has_no_history(tmp_path):
    m = und.turn_message([], cst.empty(), "안녕", PICK, _s(tmp_path), EL)
    assert PICK["headers"]["history"] not in m and m.endswith(PICK["headers"]["utterance"] + "\n안녕")


def test_call_stdin_and_usage(tmp_path, fake_cli):
    bin_, route, log = fake_cli
    set_out(route, [("concern", "ZZC-1")], thinking=0)
    s = _s(tmp_path, claude_cli_bin=bin_)
    r = asyncio.run(und.call(s, "SYS", "메시지본문"))
    assert r.error is None and '"ZZC-1"' in r.output_raw and r.usage["thinking_tokens"] == 0 and r.api_ms == 900
    rec = read_log(log)[-1]
    assert rec["stdin"] == "메시지본문" and "메시지본문" not in rec["argv"]  # stdin 으로 감
    assert rec["env_thinking"] == "0" and rec["cwd"].endswith("cwd")


def test_call_timeout_and_exit_code(tmp_path, fake_cli, monkeypatch):
    bin_, route, _ = fake_cli
    set_out(route, [("concern", "ZZC-1")])
    monkeypatch.setenv("FAKE_CLI_SLEEP", "2")
    r = asyncio.run(und.call(_s(tmp_path, claude_cli_bin=bin_, chat_pick_timeout_sec=0.5), "S", "m"))
    assert r.error["kind"] == "timeout"
    monkeypatch.delenv("FAKE_CLI_SLEEP")
    monkeypatch.setenv("FAKE_CLI_EXIT", "3")
    r = asyncio.run(und.call(_s(tmp_path, claude_cli_bin=bin_), "S", "m"))
    assert r.error["kind"] == "cli" and "exit=3" in r.error["detail"]
