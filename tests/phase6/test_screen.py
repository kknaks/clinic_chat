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
    assert kinds[-1] == "run_done" and kinds.count("case_done") == 3 and "cost" in kinds
    assert "word_check" not in kinds and "judge" not in kinds  # 자동 채점 끔(기본) — 「채점」 버튼으로
    assert "stage" in kinds and "text" in kinds and "trace" in kinds
    opus_before = [p for p in client.deps.anthropic.calls if p["model"].startswith("claude-opus")]
    assert opus_before == []
    graded = client.post(f"/api/runs/{rid}/quality").json()["record"]
    assert graded["judge_order"] and all("not_found" in graded["cases"][c]["word_check"] for c in graded["cases"])
    assert graded["cost"]["quality_usd"] > 0 and graded["cost"]["total_usd"] > graded["cost"]["answers_usd"]
    raw = (client.deps.settings.runs_path / "runs.jsonl").read_text(encoding="utf-8")
    assert json.loads(raw.splitlines()[-1])["judge_order"] == []  # 기록 줄은 그대로 · 채점은 quality.jsonl
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
        assert cs["word_check"] is None and cs["judge"] is None  # 자동 채점 끔
        assert cs["cost"]["usd"] == rec["cost"]["cases"][c]
    assert s["scale"] >= max(rec["cases"][c]["timing"]["total_ms"] for c in rec["cases"])
    assert s["cost"]["answers_usd"] == rec["cost"]["answers_usd"]


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


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_stage_legend_items():
    """범례 = 단계마다 색 · 이름 · 시간(같은 이름 여러 번이면 순서대로 각각) — 글자 견본(▩) 없음."""
    script = (
        "const S=require(process.argv[1]);console.log(JSON.stringify([S.legendItems(["
        "{name:'hub_resolve',start_ms:0,end_ms:3.2},{name:'jev',start_ms:3.2,end_ms:823.2},"
        "{name:'hub_path',start_ms:823.2,end_ms:830},{name:'claude',start_ms:830,end_ms:9530}]),"
        "S.legendItems([{name:'claude#1',start_ms:0,end_ms:1000},{name:'tool:raw_get#1',start_ms:1000,end_ms:1012},"
        "{name:'tool:raw_get#2',start_ms:1000,end_ms:1040},{name:'claude#2',start_ms:1040,end_ms:null}]),"
        "S.fmtDur(999.6),S.fmtDur(8700)]))"
    )
    c3, c1, a, b = json.loads(subprocess.run([NODE, "-e", script, str(ROOT / "web" / "state.js")],
                                             capture_output=True, text=True, check=True).stdout)
    assert [(x["kind"], x["label"], x["dur"]) for x in c3] == [
        ("hub", "hub", "3ms"), ("jev", "jev", "820ms"), ("hub", "hub", "7ms"), ("claude", "claude", "8.7s")]
    assert [(x["kind"], x["label"], x["dur"]) for x in c1] == [
        ("claude", "claude#1", "1.0s"), ("tool", "raw_get#1", "12ms"), ("tool", "raw_get#2", "40ms"),
        ("claude", "claude#2", "…")]
    assert (a, b) == ("1000ms", "8.7s")
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert not any(ch in js for ch in "▩▓▏")


def _lum(hexc: str) -> float:
    rgb = [int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def test_stage_colors_contrast_light_dark():
    """단계 색(막대 · 범례 점) — 라이트 · 다크 둘 다 패널 · 막대 바탕과 3:1 이상, 최소 폭 3px."""
    import re

    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    light = css[css.index(":root {"):css.index("@media (prefers-color-scheme: dark)")]
    dark = css[css.index("@media (prefers-color-scheme: dark)"):]
    for block in (light, dark):
        tok = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", block))
        for k in ("claude", "tool", "hub", "jev"):
            for bg in ("panel", "bg"):
                assert _contrast(tok[k], tok[bg]) >= 3.0, (k, bg, tok[k], tok[bg])
    assert "min-width: 3px" in css


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_cost_state_and_format():
    """비용 표시 — 열마다(case_done.cost) · 합계(cost 이벤트) · 채점 비용(quality_cost) · 기록에서 다시 그림."""
    script = (
        "const S=require(process.argv[1]);const s=S.initial('r','q');"
        "S.reduce(s,'case_done',{case:'case1',timing:{stages:[]},cost:{usd:0.0444,by_stage:{}}});"
        "S.reduce(s,'cost',{cases:{case1:0.0444},answers_usd:0.1037,quality_usd:null,total_usd:0.1037});"
        "S.reduce(s,'quality_cost',{cost_usd:0.21});"
        "const r=S.fromRecord({run_id:'x',question:'q',cases:{case1:{answer:'a',cost:{usd:0.01}}},cost:{answers_usd:0.01}});"
        "console.log(JSON.stringify([s.cases.case1.cost.usd,s.cost.answers_usd,s.cost.quality_usd,r.cases.case1.cost.usd,"
        "r.cost.answers_usd,S.fmtUsd(0.0042),S.fmtUsd(0.1037),S.fmtUsd(null)]))"
    )
    out = json.loads(subprocess.run([NODE, "-e", script, str(ROOT / "web" / "state.js")], capture_output=True,
                                    text=True, check=True).stdout)
    assert out == [0.0444, 0.1037, 0.21, 0.01, 0.01, "$0.0042", "$0.104", "–"]


def test_grade_button_and_cost_columns(client):
    page = client.get("/").text
    assert 'id="grade"' in page and "예상 $" in page and "<th>채점</th>" in page
    js = client.get("/static/app.js").text
    assert "/quality`" in js and "S.fmtUsd(" in js
