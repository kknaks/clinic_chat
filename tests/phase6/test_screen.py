"""Phase 6 — 화면: 서버 API(SSE 계약 §4-3) · SSE 이벤트 → 화면 상태(web/state.js 를 node 로 · 모의 이벤트 재생)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clinic_chat.server import create_app
from tests import mock_server

ROOT = Path(__file__).resolve().parent.parent.parent
NODE = shutil.which("node")


def sse_events(text: str) -> list[tuple[str, dict]]:
    out, ev = [], None
    for line in text.splitlines():
        if line.startswith("event:"):
            ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and ev:
            out.append((ev, json.loads(line.split(":", 1)[1].strip())))
            ev = None
    return out


@pytest.fixture
def client(tmp_path):
    deps, hub = mock_server.build(str(tmp_path / "runs"), realtime=False)
    with TestClient(create_app(deps, before_start=hub.start)) as c:
        c.deps = deps
        yield c


def ask(client, q="소프웨이브 어떻게 해요?"):
    rid = client.post("/api/ask", json={"question": q}).json()["run_id"]
    with client.stream("GET", f"/api/runs/{rid}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    return rid, sse_events(body)


def test_sse_contract_and_records(client):
    st = client.get("/api/status").json()
    assert st["gold_mcp"] == 9 and st["raw_mcp"] == 3 and st["cases"] == {"case1": None, "case2": None, "case3": None}
    rid, evs = ask(client)
    kinds = [e for e, _ in evs]
    assert kinds[-1] == "run_done" and kinds.count("case_done") == 3 and kinds.count("word_check") == 3
    assert kinds.count("judge") == 1 and "stage" in kinds and "text" in kinds and "trace" in kinds
    assert kinds.index("judge") > max(i for i, k in enumerate(kinds) if k == "case_done")  # 셋 다 끝난 뒤 ① → ②
    for e, d in evs:
        if e in ("stage", "text", "trace", "case_done", "word_check"):
            assert d["case"] in ("case1", "case2", "case3")
    rec = client.get(f"/api/runs/{rid}").json()["record"]
    assert rec["run_id"] == rid and rec["judge_order"]
    lst = client.get("/api/runs?limit=5").json()["runs"]
    assert lst[0]["run_id"] == rid and set(lst[0]["cases"]) == {"case1", "case2", "case3"}
    assert client.post(f"/api/runs/{rid}/human_check", json={"case": "case1", "verdict": "맞음", "note": "x"}).status_code == 200
    assert client.post(f"/api/runs/{rid}/human_check", json={"case": "case1", "verdict": "몰라"}).status_code == 422
    assert client.get("/api/runs").json()["runs"][0]["cases"]["case1"]["human"] == "맞음"
    rr = client.post(f"/api/runs/{rid}/rerun").json()
    assert len(rr["run_ids"]) == 3
    for x in rr["run_ids"]:
        with client.stream("GET", f"/api/runs/{x}/events") as r:
            assert sse_events("".join(r.iter_text()))[-1][0] == "run_done"
    assert sum(1 for r in client.get("/api/runs").json()["runs"] if r["rerun_of"] == rid) == 3
    assert client.get("/").status_code == 200 and client.get("/static/state.js").status_code == 200


def test_no_key_cases(tmp_path):
    deps, hub = mock_server.build(str(tmp_path / "runs"), realtime=False)
    deps.anthropic = None
    deps.jev = None
    with TestClient(create_app(deps, before_start=hub.start)) as c:
        st = c.get("/api/status").json()
        assert st["cases"] == {"case1": "키 없음", "case2": "키 없음", "case3": "키 없음"}
        _, evs = ask(c)
        done = {d["case"]: d for e, d in evs if e == "case_done"}
        assert all(done[x]["error"] == "키 없음" for x in done)


def replay(events: list, disabled: dict | None = None) -> dict:
    script = (
        "const S=require(process.argv[1]);const inp=JSON.parse(require('fs').readFileSync(0,'utf8'));"
        "const s=S.initial('r1','q',inp.disabled);for(const [e,d] of inp.events)S.reduce(s,e,d);"
        "s.scale=S.scale(s);s.nf=S.CASES.map(c=>S.notFound(s.cases[c]));console.log(JSON.stringify(s));"
    )
    out = subprocess.run([NODE, "-e", script, str(ROOT / "web" / "state.js")], input=json.dumps(
        {"events": events, "disabled": disabled}), capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_state_replay_from_mock_events(client):
    rid, evs = ask(client)
    s = replay(evs)
    rec = client.get(f"/api/runs/{rid}").json()["record"]
    assert s["done"] is True
    for c in ("case1", "case2", "case3"):
        cs, rc = s["cases"][c], rec["cases"][c]
        assert cs["text"] == rc["answer"] and cs["done"] is True
        assert cs["timing"]["total_ms"] == rc["timing"]["total_ms"]
        assert [x["name"] for x in cs["stages"]] == [x["name"] for x in rc["timing"]["stages"]]
        assert cs["word_check"]["not_found"] == rc["word_check"]["not_found"]
        assert cs["judge"]["pass_count"] == rc["judge"]["pass_count"]
    assert s["scale"] >= max(rec["cases"][c]["timing"]["total_ms"] for c in rec["cases"])
    assert s["judge_order"] == rec["judge_order"]


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_state_no_key_shown():
    s = replay([("case_done", {"case": "case3", "timing": {"stages": []}, "error": "키 없음"}), ("run_done", {})],
               disabled={"case1": "키 없음", "case2": "키 없음"})
    assert [s["cases"][c]["error"] for c in ("case1", "case2", "case3")] == ["키 없음"] * 3


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_state_medians_and_from_record():
    script = (
        "const S=require(process.argv[1]);const mk=(a,b)=>{const s=S.initial();for(const c of S.CASES)"
        "s.cases[c].timing={ttft_ms:a,total_ms:b};return s};"
        "console.log(JSON.stringify([S.medians([mk(1,10),mk(3,30),mk(2,20)]),"
        "S.fromRecord({run_id:'x',question:'q',cases:{case1:{answer:'a',timing:{total_ms:5,stages:[]},"
        "word_check:{not_found:2}}},judge_order:['case1']})]))"
    )
    m, s = json.loads(subprocess.run([NODE, "-e", script, str(ROOT / "web" / "state.js")], capture_output=True,
                                     text=True, check=True).stdout)
    assert m["case1"] == {"n": 3, "ttft_ms": 2, "total_ms": 20}
    assert s["cases"]["case1"]["text"] == "a" and s["cases"]["case1"]["word_check"]["not_found"] == 2
    assert s["cases"]["case2"]["judge"] is None


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_follow_scroll_and_new_question_clears():
    """화면 피드백(10-30): 답 영역 따라가기는 바닥 근처일 때만 · 새 질문이면 세 열을 비운 상태에서 시작."""
    script = (
        "const S=require(process.argv[1]);"
        "const old=S.initial('r0','옛 질문');S.reduce(old,'text',{case:'case1',delta:'옛 답'});"
        "S.reduce(old,'case_done',{case:'case1',timing:{total_ms:9,stages:[]}});"
        "S.reduce(old,'word_check',{case:'case1',not_found:1,names:[]});"
        "const nu=S.initial(null,'새 질문');"
        "console.log(JSON.stringify({b1:S.atBottom(600,400,1000),b2:S.atBottom(590,400,1000),"
        "up:S.atBottom(100,400,1000),nu:nu,old_text:old.cases.case1.text}))"
    )
    out = json.loads(subprocess.run([NODE, "-e", script, str(ROOT / "web" / "state.js")], capture_output=True,
                                    text=True, check=True).stdout)
    assert out["b1"] is True and out["b2"] is True and out["up"] is False
    assert out["old_text"] == "옛 답"
    for c in ("case1", "case2", "case3"):
        cs = out["nu"]["cases"][c]
        assert cs["text"] == "" and cs["timing"] is None and cs["word_check"] is None and cs["judge"] is None
        assert cs["stages"] == [] and cs["trace"] is None
    assert out["nu"]["question"] == "새 질문"


def test_page_layout_contract(client):
    """답 영역 고정 높이 · 안에서 스크롤 · 보낸 뒤 입력창 비움 · 펼침 · 채점은 답 아래."""
    css = client.get("/static/style.css").text
    js = client.get("/static/app.js").text
    assert "height: 60vh" in css and "overflow-y: auto" in css
    assert '$("q").value = "";' in js and "resetView();" in js
    assert 'class: "below"' in js and "S.atBottom(" in js
    page = client.get("/").text
    assert 'id="current"' in page
    assert 'class="warn"' not in page  # 경고 줄은 화면에서 뺌(10-30 사용자) — 규칙은 README · SPEC 에
