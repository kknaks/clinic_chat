"""WP-1 — 화면 바탕(T8 이스케이프 · T17 문구 0 · T24 조각 / 전체 · 인라인 0 · data-slot) · 가짜 턴 줄기(E0 꼴)."""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

from fastapi.testclient import TestClient

from clinic_chat.config import ROOT
from clinic_chat.consult.fake_turn import make
from clinic_chat.consult.render import TEMPLATES, Render
from clinic_chat.consult.server import create_app
from tests.conftest import make_settings

CONSULT = ROOT / "clinic_chat" / "consult"
STATIC = CONSULT / "static"
HANGUL = re.compile(r"[가-힣]")
# SPEC-004 §4-5 의 자리(+ step-<코드> · error-<종류> 는 조각에서)
# 0.5(SPEC-010 WP-2) — 왼쪽 리스트 · 점 자리가 진료항목 목록으로 바뀌어 list-empty · face-empty → items-empty(IL9)
SLOTS = ("page-title", "face-title", "list-title", "chat-title", "input-placeholder", "send-label", "items-empty")
MODAL_SLOTS = ("pm-empty", "pm-effect-course")  # 0.5 시술 설명 모달 빈 틀(§4-5 · MD2 · N3)
VENDORED = {  # 본(CENTURION_DAY backend/day/static · dev c5045a99)과 같은 sha256(E0-6)
    "htmx.min.js": "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447",
    "HTMX-LICENSE.txt": "23410c2d962ac773ec1bf4137debd7cd0d914a3bb654aff9250dde7c278c3da1",
    "htmx-ext-sse.js": "3b5992a541619babefc4c169505af474df5c3039da51e59b96ccf9241ecd61d2",
    "HTMX-EXT-SSE-LICENSE.txt": "a17d224ce9afdc21ec9491b76a2f5d036d19aa641ac4f85ca29c21f9f7e54fdf",
    "PretendardVariable.woff2": "9599f12fd42fc0bce1cd50b47a0c022e108d7aa64dd0d1bb0ed44f3282d900b4",
    "PRETENDARD-LICENSE.txt": "b04538c9abec39a3db75108cf0af0fd9c77032fe8aa2cf38345b4d250e98e38e",
    "medikit.css": "37ee05c8d6331facdb9ffe46689cbaaf14a54f6bc5cbab68946c3aa00514c772",
    "medikit.js": "bf63fd2490b104a726fa232caf9827975771edf0acb8220cbd1d637bf8ecae40",
}


def app(tmp_path, **kw):
    s = make_settings(tmp_path, chat_dir=str(tmp_path / "chat"), chat_fake_turn=True, **kw)
    return create_app(s, turn_fn=make(0))


def test_t24_full_page_vs_fragment(tmp_path):
    c = TestClient(app(tmp_path))
    full = c.get("/").text
    frag = c.get("/", headers={"HX-Request": "true"}).text
    assert full.lstrip().lower().startswith("<!doctype html>") and "<head>" in full
    assert "<head>" not in frag and 'id="consult"' in frag
    for slot in SLOTS:
        assert f'data-slot="{slot}"' in full, slot
    # 빈 대화 = 기본 자리 수(라벨 · 묶음 머리는 값이 올 때) + 0.3 A8 드로어 빈 자리 넷(new-label · toggle-label ·
    # list-title · empty — 코디 W-2: 사용자 결정 A8 로 바뀌는 예외)
    assert len(re.findall(r'data-slot="', full)) == len(SLOTS) + 4 + len(MODAL_SLOTS)
    assert 'allowEval":false' in full and 'allowScriptTags":false' in full
    cid = re.search(r'data-conv-id="([^"]+)"', full).group(1)
    assert "<head>" not in c.get(f"/c/{cid}", headers={"HX-Request": "true"}).text
    assert c.get("/face-check").status_code == 200


def _template_files() -> list[Path]:
    return [p for p in TEMPLATES.rglob("*") if p.is_file()]


def test_t24_no_inline_script_style_or_handlers():
    for p in _template_files():
        t = p.read_text(encoding="utf-8")
        assert "style=" not in t, p.name
        assert not re.search(r"\son[a-z]+=", t), p.name
        for m in re.finditer(r"<script([^>]*)>(.*?)</script>", t, re.S):
            assert "src=" in m.group(1) and not m.group(2).strip(), p.name


def _strip_comments(text: str, kind: str) -> str:
    if kind == "py":
        text = re.sub(r'(?s)""".*?"""', "", text)
        return "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())
    if kind in ("css", "js"):
        return re.sub(r"(?s)/\*.*?\*/", "", text)
    return re.sub(r"(?s)\{#.*?#\}", "", text)


def test_t17_no_hangul_sentences_in_consult_code_and_templates():
    """T17 — 주석 · data-slot 이름 빼고 한글 0(W-7: 머리 · 표식 · 「(없음)」 은 prompts/consult.json 에 있다)."""
    bad = []
    trial_input = {"e3.py"}  # W-7 예외 — 실측 E3 의 질문 세 줄(시험 자료 · 화면에 보일 문장 아님)
    for p in [*CONSULT.glob("*.py"), *_template_files(), STATIC / "consult.css", STATIC / "consult.js"]:
        if p.name in trial_input:
            continue
        kind = {".py": "py", ".css": "css", ".js": "js"}.get(p.suffix, "html")
        body = _strip_comments(p.read_text(encoding="utf-8"), kind)
        if HANGUL.search(body):
            bad.append(p.name)
    assert bad == []


def test_t8_escape(tmp_path):
    r = Render()
    assert "<b>" not in r.delta("<b>x</b>") and "&lt;b&gt;" in r.delta("<b>x</b>")
    assert "<i>" not in r.answer(1, "<i>a</i>", None)
    lst = {"concerns": [{"concern_code": "C", "concern_name": "<s>c</s>", "inherited_from": None,
                         "modalities": [{"modality_name": "<u>m</u>", "procedures": [
                             {"procedure_code": "P", "procedure_name": "<p>x</p>", "reach": ["<r>"],
                              "concern_match": [], "gids": []}]}], "modality_only": [], "unreached": [], "counts": {}}]}
    out = r.list(lst)
    assert "<s>" not in out and "<u>" not in out and "<p>x" not in out and "<r>" not in out
    c = TestClient(app(tmp_path))
    cid = re.search(r'data-conv-id="([^"]+)"', c.get("/").text).group(1)
    assert "<x>" not in c.post(f"/c/{cid}/turns", data={"text": "<x>hi"}).text


def test_e0_fake_turn_stream_order_and_close(tmp_path):
    a = app(tmp_path)
    c = TestClient(a)
    cid = re.search(r'data-conv-id="([^"]+)"', c.get("/").text).group(1)
    frag = c.post(f"/c/{cid}/turns", data={"text": "q"}).text
    assert 'sse-connect="/c/' in frag and 'sse-close="close"' in frag
    for ev in ("step", "delta", "done", "turn_error", "items"):  # 0.5 — 왼쪽은 `items` 하나(list · sites 받는 자리 없음)
        assert f'sse-swap="{ev}"' in frag
    assert 'sse-swap="list"' not in frag and 'sse-swap="sites"' not in frag
    asyncio.run(asyncio.sleep(0))
    body = c.get(f"/c/{cid}/turns/1/events").text
    kinds = re.findall(r"^event: (\S+)$", body, re.M)
    main = [k for k in kinds if k != "step"]  # 수정 1 — 단계 줄은 단계가 바뀔 때마다 다시 온다
    assert kinds[0] == "step" and main[:1] == ["items"] and kinds[-2:] == ["done", "close"]
    assert kinds.index("items") < kinds.index("delta") and kinds[-3] == "step"
    for block in body.strip().split("\n\n"):  # W-3 ② ⑤ — 이벤트마다 data: 줄 · 글자 > 0
        datas = [ln[6:] for ln in block.splitlines() if ln.startswith("data: ")]
        assert datas and "".join(datas).strip()
    # 다시 붙기 — Last-Event-ID 다음부터(EV5 — done 이 통째라 끝 꼴은 같다)
    again = c.get(f"/c/{cid}/turns/1/events", headers={"Last-Event-ID": "5"}).text
    assert re.findall(r"^id: (\d+)$", again, re.M)[0] == "6" and "event: done" in again


def test_e0_fake_turn_error_shape(tmp_path):
    c = TestClient(app(tmp_path))
    cid = re.search(r'data-conv-id="([^"]+)"', c.get("/").text).group(1)
    c.post(f"/c/{cid}/turns", data={"text": "make error"})
    body = c.get(f"/c/{cid}/turns/1/events").text
    kinds = re.findall(r"^event: (\S+)$", body, re.M)
    assert kinds[-2:] == ["turn_error", "close"] and "list" not in kinds and 'data-error="fake"' in body


def test_e0_6_vendored_files_match_source():
    for name, sha in VENDORED.items():
        assert hashlib.sha256((STATIC / name).read_bytes()).hexdigest() == sha, name
    notice = (STATIC / "MEDIKIT-NOTICE.txt").read_text(encoding="utf-8")
    assert VENDORED["medikit.css"] in notice and VENDORED["medikit.js"] in notice
    assert "@font-face" in (STATIC / "consult.css").read_text(encoding="utf-8")  # 검수 W-5


def test_tf1_rejects_empty(tmp_path):
    c = TestClient(app(tmp_path))
    cid = re.search(r'data-conv-id="([^"]+)"', c.get("/").text).group(1)
    assert 'data-error="empty"' in c.post(f"/c/{cid}/turns", data={"text": "  "}).text
