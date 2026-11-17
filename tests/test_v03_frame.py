"""0.3 WP-1 — 화면 네 칸 · 드로어 · 객체 패널(SPEC-006 §8A SV1 ~ SV11 · S6-T27 ~ T29 · S6-T23 · 코디 W-11 · 검수 N-9)."""

from __future__ import annotations

import html
import json
import re

from fastapi.testclient import TestClient

from clinic_chat.consult.drawer import DrawerIndex
from clinic_chat.consult.fake_turn import make
from clinic_chat.consult.ledger import TURNS_FILE, Ledger
from clinic_chat.consult.render import TEMPLATES
from clinic_chat.consult.server import create_app
from tests.conftest import make_settings, read_log
from tests.state_fixture import Run

HANGUL = re.compile(r"[가-힣]")
NEW_TEMPLATES = ("drawer.html", "drawer_list.html", "state.html", "state_turn.html")


def _w(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _start(cid, n, q, at, **kw):
    return {"conv_id": cid, "turn": n, "phase": "start", "asked_at": at, "question": q, "llm": {"session_id": "S"},
            "consult_state": kw.pop("state", "on"), **kw}


def _end(cid, n, **kw):
    return {"conv_id": cid, "turn": n, "phase": "end", "error": None, "llm": {"answered": True}, **kw}


def _app(tmp_path, **kw):
    kw.setdefault("chat_state", "on")
    s = make_settings(tmp_path, chat_dir=str(tmp_path / "chat"), chat_fake_turn=True, **kw)
    return create_app(s, turn_fn=make(0))


def _items(text):
    return re.findall(r'<li class="cs-drawer__item" data-conv-state="([^"]+)">\s*<a class="cs-drawer__link" '
                      r'href="/c/([^"]+)" data-conv-id="[^"]+"( aria-current="page")?>\s*'
                      r'<span class="cs-drawer__q">([^<]*)</span>', text)


def test_s6_t27_drawer_list_order_limit_and_marks(tmp_path):
    f = tmp_path / "chat" / TURNS_FILE
    _w(f, [_start("aaa", 1, "first-a", "2026-11-17T10:00:00+09:00", consult_rev=3), _end("aaa", 1),
           _start("bbb", 1, "first-b", "2026-11-17T11:00:00+09:00", consult_rev=3), _end("bbb", 1),
           _start("aaa", 2, "second-a", "2026-11-17T12:00:00+09:00", consult_rev=3), _end("aaa", 2),
           _start("ccc", 1, "first-c", "2026-11-17T11:30:00+09:00", consult_rev=3)])  # 맺는 줄 없음 → broken
    c = TestClient(_app(tmp_path))
    page = c.get("/c/bbb").text
    items = _items(page)
    # 마지막 턴 시각 내림차순 · 항목 = 첫 질문 · /c/<id> · 지금 대화에 aria-current · 맺는 줄 없는 대화 = broken
    assert [(i[1], i[3]) for i in items] == [("aaa", "first-a"), ("ccc", "first-c"), ("bbb", "first-b")]
    assert [i[0] for i in items] == ["open", "broken", "open"] and [bool(i[2]) for i in items] == [False, False, True]
    assert '<time class="cs-drawer__at" datetime="2026-11-17T12:00:00+09:00">11-17 12:00</time>' in page
    # 턴 0 인 대화(GET / 가 만든 빈 대화)는 목록에 없다
    loc = c.get("/", follow_redirects=False).headers["location"]
    assert loc[3:] not in c.get("/convs").text and len(_items(c.get("/convs").text)) == 3
    # 「새 대화」 = / · 빈 자리 넷(문구 0)
    for slot in ("drawer-new-label", "drawer-list-title", "drawer-toggle-label"):
        assert f'data-slot="{slot}"' in page
    assert 'href="/" data-slot="drawer-new-label"' in page
    # CHAT_DRAWER_MAX — 앞의 것만
    c2 = TestClient(_app(tmp_path, chat_drawer_max=2))
    assert [i[1] for i in _items(c2.get("/convs?current=aaa").text)] == ["aaa", "ccc"]
    assert 'aria-current="page"' in c2.get("/convs?current=aaa").text


def test_s6_t27_empty_drawer_and_index_not_rereading_file(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path))
    page = c.get(c.get("/", follow_redirects=False).headers["location"]).text
    assert 'data-slot="drawer-empty"' in page
    # W-11 — 뜰 때 한 번 훑은 색인 · 줄을 쓸 때 함께 고침 · 목록을 그릴 때 파일을 다시 읽지 않는다
    calls = []
    orig = DrawerIndex.scan.__func__

    def spy(cls, path):
        calls.append(path)
        return orig(cls, path)
    monkeypatch.setattr(DrawerIndex, "scan", classmethod(spy))
    monkeypatch.setattr(Ledger, "rows", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rows")))
    a = _app(tmp_path)
    assert len(calls) == 1
    led = a.state.ctx.ledger
    conv = led.new_conv(None)
    led.start(conv, "q-live", "on")
    led.end(conv, {"turn": 1, "error": None, "llm": {"answered": True}})
    c = TestClient(a)
    for _ in range(3):
        assert "q-live" in c.get("/convs").text
    assert len(calls) == 1


def test_s6_t27_outdated_and_limit_states(tmp_path):
    f = tmp_path / "chat" / TURNS_FILE
    _w(f, [_start("old02", 1, "q-old", "2026-11-17T09:00:00+09:00"), _end("old02", 1),  # 0.2 켠 대화(rev 없음)
           _start("off01", 1, "q-off", "2026-11-17T09:10:00+09:00", state="off"), _end("off01", 1),
           _start("brk", 1, "q-brk", "2026-11-17T09:20:00+09:00", consult_rev=3), _end("brk", 1, conv_end="broken")])
    states = {i[1]: i[0] for i in _items(TestClient(_app(tmp_path)).get("/convs").text)}
    assert states == {"old02": "outdated", "off01": "outdated", "brk": "broken"}
    assert {i[1]: i[0] for i in _items(TestClient(_app(tmp_path, chat_state="off")).get("/convs").text)}["off01"] \
        == "open"


def test_s6_t28_open_from_drawer_resumes_same_session(tmp_path, fake_cli):
    r1 = Run(tmp_path, fake_cli)
    try:
        row1, _, _ = r1.turn("가짜고민 알려줘")
    finally:
        r1.close()
    r2 = Run(tmp_path, fake_cli)  # 서버를 다시 띄움 — 드로어에서 지난 대화를 연 꼴
    try:
        assert f'href="/c/{r1.cid}"' in r2.c.get("/convs").text
        page = r2.c.get(f"/c/{r1.cid}").text
        assert "가짜고민 알려줘" in page and f'data-conv-id="{r1.cid}"' in page
        r2.cid, r2.n = r1.cid, 1
        row2, _, _ = r2.turn("가짜시술 개요")
        cli = [x for x in read_log(r2.log) if "-p" in x["argv"]][-1]["argv"]
        assert row2["llm"]["mode"] == "resume" and cli[cli.index("--resume") + 1] == row1["llm"]["session_id"]
        new = [x["n"] for x in row2["material"]["new_lines"]]
        assert all(n >= row1["material"]["next_n"] for n in new)  # 줄 번호가 장부에서 이어진다
        assert row2["material"]["next_n"] >= row1["material"]["next_n"]
    finally:
        r2.close()


def test_s6_t29_panel_on_state_event_before_done(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, chat_debug_panel="on")
    try:
        assert 'id="consult-state"' in r.c.get(f"/c/{r.cid}").text
        row, kinds, body = r.turn("가짜고민 알려줘")
        assert "state" in kinds and kinds.index("state") == kinds.index("done") - 1
        block = body.split("event: state\n", 1)[1].split("\n\n", 1)[0]
        frag = html.unescape("\n".join(ln[6:] for ln in block.splitlines() if ln.startswith("data: ")))
        for k in ("after", "diff", "blanks", "ask"):
            assert f'data-key="{k}"' in frag
            assert json.dumps(row["consult"][k], ensure_ascii=False, indent=1) in frag  # 장부 글자 그대로
        page = r.c.get(f"/c/{r.cid}").text  # 화면을 열 때는 장부의 성공 턴으로 다시 그린다
        assert 'class="cs-state__turn" data-turn="1"' in page and 'sse-swap="state"' not in page
    finally:
        r.close()


def test_s6_t29_panel_off_no_column_no_event(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli)
    try:
        assert 'id="consult-state"' not in r.c.get(f"/c/{r.cid}").text
        _, kinds, _ = r.turn("가짜고민 알려줘")
        assert "state" not in kinds and kinds[-2:] == ["done", "close"]
    finally:
        r.close()


def test_s6_t29_off_conv_panel_is_empty_bundle(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, state="off", chat_debug_panel="on")
    try:
        _, kinds, body = r.turn("가짜고민 알려줘")
        block = body.split("event: state\n", 1)[1].split("\n\n", 1)[0]
        assert "cs-state__fields" not in block and kinds.index("state") == kinds.index("done") - 1
    finally:
        r.close()


def test_s6_t23_new_templates_no_hangul():
    for name in NEW_TEMPLATES:
        body = re.sub(r"(?s)\{#.*?#\}", "", (TEMPLATES / "partials" / name).read_text(encoding="utf-8"))
        assert not HANGUL.search(body), name
        assert "<script" not in body and "style=" not in body
