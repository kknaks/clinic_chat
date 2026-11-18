"""WP-7 · WP-4 · WP-9 — 한 턴 흐름 · 라우트 · 이벤트 · 장부(T6 · T7 · T16 · T20 · T21 · T22) — 가짜 셋(CLI · 허브 · Jev)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clinic_chat.columns.base import Deps
from clinic_chat.columns.col_b import TAIL_MARK
from clinic_chat.consult.ledger import BOLD, Ledger
from clinic_chat.consult.server import create_app
from tests.conftest import make_settings, read_log, stream
from tests.consult_fixture import consult_hub
from tests.fake_jev import FakeJev
from tests.v04_fixture import out, set_map
from tests.walk_fixture import jev_noul

ANSWER = "짧은 답 <b>\n되묻기?\n" + TAIL_MARK + "\n1: 1\n"
_OPEN: list[TestClient] = []


@pytest.fixture(autouse=True)
def _close_clients():
    """TestClient 는 컨텍스트 안에서만 이벤트 루프 하나를 유지한다(배경 턴이 요청 사이에 산다)."""
    yield
    while _OPEN:
        _OPEN.pop().__exit__(None, None, None)


def kinds_noul(name, instr, state):
    q = state.get("question", "")
    kinds = {"indication": 0.9} if "고민" in q else {"overview": 0.9}
    return jev_noul(kinds=kinds, target=lambda i, st: 0.9)(name, instr, state)


UMAP = {"고민": out([("concern", "ZZC-1")], kinds=["indication"]),
        "가짜시술둘": out([("procedure", "ZZP-2")], kinds=["overview"]),
        "가짜시술": out([("procedure", "ZZP-1")], kinds=["overview"]), "*": out([])}  # 0.4 — 가짜 이해 출력(이번 말 낱말)


def make(tmp_path, fake_cli, *, hub=None, noul=kinds_noul, umap=None, **kw):
    bin_, route, log = fake_cli
    (route / "answer.jsonl").write_text(stream(ANSWER), encoding="utf-8")
    set_map(route, {**UMAP, **(umap or {})})
    kw.setdefault("chat_pick_list_cache", "off")
    s = make_settings(tmp_path, claude_cli_bin=bin_, chat_dir=str(tmp_path / "chat"),
                      chat_cli_cwd=str(tmp_path / "cwd"), **kw)
    hub = hub or consult_hub()
    fj = FakeJev(noul_fn=noul)

    def deps(st):
        return Deps(st, hub.client(), fj.jev(st))
    app = create_app(s, deps_factory=deps)
    c = TestClient(app)
    c.__enter__()
    _OPEN.append(c)
    return app, c, fj, log


def new_conv(c) -> str:
    return re.search(r'data-conv-id="([^"]+)"', c.get("/").text).group(1)


def turn(c, cid, text, n):
    frag = c.post(f"/c/{cid}/turns", data={"text": text}).text
    body = c.get(f"/c/{cid}/turns/{n}/events").text
    return frag, body, re.findall(r"^event: (\S+)$", body, re.M)


def rows(tmp_path, phase="end"):
    f = tmp_path / "chat" / "turns.jsonl"
    return [r for r in map(json.loads, f.read_text(encoding="utf-8").splitlines()) if r["phase"] == phase]


def test_t6_order_t7_tail_hidden_and_ledger(tmp_path, fake_cli):
    app, c, fj, log = make(tmp_path, fake_cli)
    cid = new_conv(c)
    frag, body, kinds = turn(c, cid, "가짜고민 시술", 1)
    assert 'sse-connect="/c/' in frag
    first_delta = kinds.index("delta")
    assert kinds.index("list") < kinds.index("sites") < first_delta and kinds[-2:] == ["done", "close"]
    for block in body.strip().split("\n\n"):
        assert "".join(ln[6:] for ln in block.splitlines() if ln.startswith("data: ")).strip()  # 글자 > 0
    assert TAIL_MARK not in body and "1: 1" not in body and "&lt;b&gt;" in body  # T7 · T8
    end = rows(tmp_path)[0]
    assert end["answer_lines"] == [{"answer_line": 1, "rows": [1]}] and TAIL_MARK in end["answer_raw"]
    assert TAIL_MARK not in end["answer"] and end["list"]["concerns"][0]["concern_code"] == "ZZC-1"
    assert end["sites"] == {"dots": [], "labels": []}  # 허브 부위 칸 없음 → 빈 꼴(점 0 · 오류 0)
    assert end["timing"]["first_list_ms"] <= end["timing"]["ttft_ms"] <= end["timing"]["total_ms"]
    assert set(end["timing"]["parts"]) >= {"understand_ms", "hub_ms", "llm_ms", "material_ms"}  # 0.4 §6-1
    for k in BOLD:  # T16 — 굵은 칸 여섯
        cur = end
        for part in k.split("."):
            assert part in cur, k
            cur = cur[part]
    assert end["llm"]["mode"] == "new" and end["session_state"] == "open"
    assert end["prompts"]["changed"] is False and end["prompts"]["consult_at_start"] == end["prompts"]["consult_now"]
    assert str(Path.home()) not in (tmp_path / "chat" / "turns.jsonl").read_text(encoding="utf-8")  # LG5
    cli = [x for x in read_log(log) if "--session-id" in x["argv"] or "--resume" in x["argv"]][-1]  # 답 실행기
    assert cli["stdin"].index("## 앱 판정") < cli["stdin"].index("## 자료") < cli["stdin"].index("## 질문")


def test_t6_out_of_scope_has_no_list_or_sites(tmp_path, fake_cli):
    app, c, fj, log = make(tmp_path, fake_cli, umap={"*": out([], oos=True)})  # out 참 길(QM3)
    cid = new_conv(c)
    _, body, kinds = turn(c, cid, "아무 말", 1)
    assert "list" not in kinds and "sites" not in kinds and kinds[-2:] == ["done", "close"]
    assert rows(tmp_path)[0]["end_state"] == "범위 밖"


def test_t16_reload_open_and_numbers_continue(tmp_path, fake_cli):
    app, c, fj, log = make(tmp_path, fake_cli)
    cid = new_conv(c)
    turn(c, cid, "가짜시술 개요", 1)
    r1 = rows(tmp_path)[0]
    led = Ledger(tmp_path / "chat")  # 서버를 다시 세운 꼴
    conv = led.get(cid)
    assert conv.chat.state == "open" and conv.chat.session_id == r1["llm"]["session_id"]
    assert conv.next_n == r1["material"]["next_n"] > 1 and conv.prompt_sha == r1["prompts"]["consult_at_start"]
    app2, c2, _, log2 = make(tmp_path, fake_cli)
    turn(c2, cid, "가짜시술둘 개요", 2)
    r2 = rows(tmp_path)[1]
    assert r2["llm"]["mode"] == "resume" and r2["material"]["new_lines"][0]["n"] == r1["material"]["next_n"]
    cli = [x for x in read_log(log2) if "--resume" in x["argv"]][-1]["argv"]
    assert cli[cli.index("--resume") + 1] == r1["llm"]["session_id"]
    page = c2.get(f"/c/{cid}").text
    assert "가짜시술 개요" in page and 'id="answer-2"' in page and "sse-connect" not in page


def test_t20_start_without_end_is_broken(tmp_path, fake_cli):
    app, c, fj, log = make(tmp_path, fake_cli)
    cid = new_conv(c)
    turn(c, cid, "가짜시술 개요", 1)
    f = tmp_path / "chat" / "turns.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"conv_id": cid, "turn": 2, "phase": "start", "question": "q", "llm": {}}) + "\n")
    conv = Ledger(tmp_path / "chat").get(cid)
    assert conv.chat.state == "broken" and conv.ended == "broken"
    _, c2, _, _ = make(tmp_path, fake_cli)
    assert 'data-error="broken"' in c2.post(f"/c/{cid}/turns", data={"text": "x"}).text
    assert 'data-error="broken"' in c2.get(f"/c/{cid}").text


def test_t21_error_turn_not_counted_and_not_carried(tmp_path, fake_cli, monkeypatch):
    app, c, fj, log = make(tmp_path, fake_cli)
    cid = new_conv(c)
    monkeypatch.setenv("FAKE_CLI_EXIT", "3")  # new 에서 실패 → 새 UUID · new 그대로
    _, body, kinds = turn(c, cid, "가짜시술 개요", 1)
    assert kinds[-2:] == ["turn_error", "close"] and 'data-error="exit"' in body
    r1 = rows(tmp_path)[0]
    assert r1["error"] and r1["session_state"] == "new" and r1["session_next"]["session_id"] != r1["llm"]["session_id"]
    conv = app.state.ctx.ledger.get(cid)
    assert conv.sent == {} and conv.next_n == 1  # 오류 턴의 재료는 「실은 gid」 · next_n 에 안 든다
    monkeypatch.delenv("FAKE_CLI_EXIT")
    turn(c, cid, "그거 더", 2)
    r2 = rows(tmp_path)[1]
    assert r2["carried"] == [] and r2["llm"]["mode"] == "new"  # 오류 턴의 대상은 든 후보가 아니다
    assert r2["material"]["new_lines"] == [] or r2["material"]["new_lines"][0]["n"] == 1


def test_t22_turn_finishes_without_subscriber(tmp_path, fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_CLI_SLEEP", "0.3")
    app, c, fj, log = make(tmp_path, fake_cli)
    cid = new_conv(c)
    c.post(f"/c/{cid}/turns", data={"text": "가짜시술 개요"})  # 줄기에 붙지 않는다
    t0 = time.time()
    while time.time() - t0 < 10 and not (tmp_path / "chat" / "turns.jsonl").exists() or not rows(tmp_path):
        time.sleep(0.05)
    assert rows(tmp_path) and rows(tmp_path)[0]["error"] is None  # 맺는 줄 · CLI 끝까지
    body = c.get(f"/c/{cid}/turns/1/events").text  # 늦게 붙어도 끝 꼴이 같다
    assert re.findall(r"^event: (\S+)$", body, re.M)[-2:] == ["done", "close"]


def test_w8_build_changed_ends_conversation(tmp_path, fake_cli):
    app, c, fj, log = make(tmp_path, fake_cli, hub=consult_hub(gold_hash="h1"))
    cid = new_conv(c)
    turn(c, cid, "가짜시술 개요", 1)
    _, c2, _, _ = make(tmp_path, fake_cli, hub=consult_hub(gold_hash="h2"))
    _, body, kinds = turn(c2, cid, "가짜시술둘 개요", 2)
    assert kinds[-2:] == ["turn_error", "close"] and 'data-error="build_changed"' in body
    assert rows(tmp_path)[1]["conv_end"] == "build_changed"
    assert 'data-error="build_changed"' in c2.post(f"/c/{cid}/turns", data={"text": "x"}).text


def test_w8_turn_limit_and_busy(tmp_path, fake_cli):
    app, c, fj, log = make(tmp_path, fake_cli, chat_max_turns=1)
    cid = new_conv(c)
    turn(c, cid, "가짜시술 개요", 1)
    assert 'data-slot="turn-limit"' in c.post(f"/c/{cid}/turns", data={"text": "x"}).text
    conv = app.state.ctx.ledger.get(cid)
    conv.running = 9
    assert 'data-error="busy"' in c.post(f"/c/{cid}/turns", data={"text": "x"}).text


def test_w8_message_over_limit_aborts_ft5_without_list(tmp_path, fake_cli):
    app, c, fj, log = make(tmp_path, fake_cli, answer_input_max_tokens=50)
    cid = new_conv(c)
    _, body, kinds = turn(c, cid, "가짜고민 시술", 1)
    r = rows(tmp_path)[0]
    assert r["end_state"] == "실행 중단" and r["reason"] == "FT5" and "list" not in kinds and r["list"] is None
    assert "done" in kinds  # 빈 재료로 LLM 을 부른다
    assert "(없음)" in r["llm_user_message"]


def test_prompt_changed_marked_and_kept_copy_used(tmp_path, fake_cli):
    src = Path(__file__).resolve().parent.parent / "prompts" / "consult.json"
    mine = tmp_path / "consult.json"
    mine.write_bytes(src.read_bytes())
    app, c, fj, log = make(tmp_path, fake_cli, chat_prompt_file=str(mine))
    cid = new_conv(c)
    turn(c, cid, "가짜시술 개요", 1)
    d = json.loads(mine.read_text(encoding="utf-8"))
    d["version"] = "0.1.1-draft"
    mine.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    turn(c, cid, "가짜시술둘 개요", 2)
    r2 = rows(tmp_path)[1]
    assert r2["prompts"]["changed"] is True and r2["prompts"]["version"] == json.loads(src.read_text(encoding="utf-8"))["version"]  # 그 대화는 시작 판으로(0.3 코디 답 — VO1 version 0.3.0-draft)


# 0.4(WORK-017 §15-4): `t12_flow_second_turn_carries_previous_target` 는 지웠다 — 든 후보(AR-9)가 없다(대화 블록 — S8-T3).
