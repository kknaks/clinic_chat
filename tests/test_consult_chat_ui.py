"""수정 1 — 채팅 화면(클로드 채팅 꼴 · 마크다운 · 스피너 · 단계 줄) · `GET /` → 303 /c/<새 id>(검수 W-1)."""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from fastapi.testclient import TestClient

from clinic_chat.config import ROOT
from clinic_chat.consult import prompt as prompt_mod
from clinic_chat.consult.fake_turn import make
from clinic_chat.consult.render import Render, md
from clinic_chat.consult.server import create_app
from clinic_chat.consult.steps import KEYS, RAW, Steps
from tests.conftest import make_settings

PROMPT = ROOT / "prompts" / "consult.json"


def settings(tmp_path):
    return make_settings(tmp_path, chat_dir=str(tmp_path / "chat"), chat_fake_turn=True)


def test_index_redirects_to_new_conversation(tmp_path):
    c = TestClient(create_app(settings(tmp_path), turn_fn=make(0)))
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and re.fullmatch(r"/c/[0-9a-f]{16}", r.headers["location"])
    r2 = c.get("/", follow_redirects=False)
    assert r2.headers["location"] != r.headers["location"]  # 매번 새 대화
    page = c.get(r.headers["location"]).text
    assert f'data-conv-id="{r.headers["location"][3:]}"' in page


def test_reload_and_restart_reach_same_conversation(tmp_path):
    s = settings(tmp_path)
    c = TestClient(create_app(s, turn_fn=make(0)))
    loc = c.get("/", follow_redirects=False).headers["location"]
    c.post(f"{loc}/turns", data={"text": "question-one"})
    asyncio.run(asyncio.sleep(0))
    c.get(f"{loc}/turns/1/events")  # 턴이 끝날 때까지
    assert "question-one" in c.get(loc).text  # 새로 고침
    c2 = TestClient(create_app(s, turn_fn=make(0)))  # 서버 다시 띄움 — 장부 되읽기(LG4)
    page = c2.get(loc).text
    assert "question-one" in page and "<strong>bold</strong>" in page


def test_steps_mapping_and_states():
    assert set(RAW.values()) <= set(KEYS)
    st = Steps()
    assert st.raw("front") and not st.raw("front")
    assert st.raw("hub") and not st.raw("depth") and not st.raw("gate")  # 허브 · 깊이 · 검사 = search 하나
    assert st.listed()
    assert st.raw("llm_wait") and not st.raw("llm")
    assert [(i["key"], i["state"]) for i in st.view()] == [
        ("understand", "done"), ("search", "done"), ("list", "done"), ("answer", "active")]
    assert st.finish() and all(i["state"] == "done" for i in st.view()) and not st.finish()
    assert not Steps().raw("unknown")


def test_step_labels_from_prompt_file_and_render():
    p = prompt_mod.load(PROMPT)
    assert set(p.steps) == set(KEYS) and all(v.strip() for v in p.steps.values())
    r = Render(p.steps)
    out = r.steps([{"key": "understand", "state": "done"}, {"key": "search", "state": "active"}])
    assert p.steps["understand"] in out and p.steps["search"] in out
    assert out.count("class=\"cs-spin ") == 1 and out.count("class=\"cs-tick") == 1
    assert 'data-step="search" data-state="active"' in out


def test_prompt_steps_key_checked():
    d = json.loads(PROMPT.read_text(encoding="utf-8"))
    del d["steps"]["list"]
    with pytest.raises(prompt_mod.PromptError):
        prompt_mod.parse(json.dumps(d).encode())
    d.pop("steps")
    assert prompt_mod.parse(json.dumps(d).encode()).steps == {}  # 없으면 단계 문구 없음(옛 보관본)


def test_markdown_is_safe():
    out = str(md("**b** <script>x</script> [l](javascript:alert(1)) ![i](http://x/i.png)\n\n- a\n- b"))
    assert "<strong>b</strong>" in out and "<li>a</li>" in out
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "javascript:" not in out.replace("[l](javascript:", "")  # 링크로 만들지 않는다
    assert "<img" not in out
    assert "<em>" not in Render().answer(1, "<em>x</em>", None)


def test_fake_stream_has_steps_spinner_and_cumulative_markdown(tmp_path):
    s = settings(tmp_path)
    labels = prompt_mod.load(PROMPT).steps
    c = TestClient(create_app(s, turn_fn=make(0)))
    loc = c.get("/", follow_redirects=False).headers["location"]
    frag = c.post(f"{loc}/turns", data={"text": "q"}).text
    assert "data-cs-spinner" in frag and 'sse-swap="delta" hx-swap="innerHTML"' in frag
    asyncio.run(asyncio.sleep(0))
    body = c.get(f"{loc}/turns/1/events").text
    blocks = [b for b in body.strip().split("\n\n") if b.strip()]
    ev = [(re.search(r"^event: (\S+)$", b, re.M).group(1), "\n".join(ln[6:] for ln in b.splitlines()
                                                                    if ln.startswith("data: "))) for b in blocks]
    steps = [d for k, d in ev if k == "step"]
    assert labels["understand"] in steps[0] and "cs-spin" in steps[0]
    assert all(labels[k] in steps[-1] for k in KEYS) and "cs-spin" not in steps[-1]  # 끝 = 모두 체크
    deltas = [d for k, d in ev if k == "delta"]
    assert len(deltas) > 3 and all(len(a) <= len(b) for a, b in zip(deltas, deltas[1:], strict=False))  # 지금까지 전체
    assert "<strong>bold</strong>" in deltas[-1] and "<li>" in deltas[-1] and "item two" in deltas[-1]
    assert "<b>" not in "".join(deltas) and "&lt;b&gt;" in deltas[-1]


def test_chat_form_shape(tmp_path):
    c = TestClient(create_app(settings(tmp_path), turn_fn=make(0)))
    page = c.get("/").text
    assert re.search(r"<textarea[^>]*name=\"text\"[^>]*data-cs-input", page)
    assert "data-cs-form" in page and "data-cs-send" in page
    log = re.search(r'<div id="chat-log"[^>]*>(.*?)</div>', page, re.S).group(1)
    assert log.strip() == ""  # 빈 대화 = 빈 자리(문구 0)
