"""WP-6 — `list` 조각 · 표지(S5-T12 · T14 · 코디 W-3 · 검수 §3-2 ①-a) — 가짜 셋 · 켠 상태 명시."""

from __future__ import annotations

import re
from pathlib import Path

from clinic_chat.consult.render import Render
from tests.state_fixture import Run, jev

CONSULT = Path(__file__).resolve().parent.parent / "clinic_chat" / "consult"
HANGUL = re.compile(r"[가-힣]")
NEW_CODE = ("state.py", "plan.py", "blanks.py", "synergy.py", "e5.py")
NEW_TEMPLATES = ("list_interest.html", "list_synergy.html", "list.html")


def _event_html(body: str, name: str) -> str:
    for block in body.strip().split("\n\n"):
        if f"event: {name}" in block:
            return "".join(ln[6:] for ln in block.splitlines() if ln.startswith("data: "))
    return ""


def test_s5_t12_subject_only_list_and_order(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev({"가짜제품": 0.9}, kinds={"overview": 0.9}))
    row, kinds, body = r.turn("가짜제품 받고 싶어요")
    assert "list" in kinds and row["list"]["interest"][0]["code"] == "ZZR-1" and row["list"]["concerns"] == []
    html = _event_html(body, "list")
    assert 'data-slot="list-section-interest"' in html and "가짜제품" in html
    assert row["timing"]["first_list_ms"] is not None
    steps = [b for b in body.split("\n\n") if "event: step" in b]
    assert any("list" in b for b in steps)  # 묶음 ① 만 있는 턴에도 「목록」 단계(검수 §3-2 ①-a)
    r.set_noul(jev({"가짜고민": 0.92, "가짜제품": 0.9}, kinds={"indication": 0.9}))
    row2, kinds2, body2 = r.turn("가짜고민 때문에요")
    assert kinds2.index("list") < kinds2.index("sites") < kinds2.index("delta")
    html2 = _event_html(body2, "list")
    assert re.search(r'data-procedure-code="ZZP-1" data-interest', html2)  # 제품 → 닿는 시술 행에 관심 표지
    assert html2.index("list-section-interest") < html2.index("cs-concern") < html2.index("list-section-synergy")
    assert 'data-slot="mark-caution"' in html2 and 'data-slot="list-synergy-outside"' in html2
    r.close()


def test_w3_modality_interest_on_group_head_only(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev(kinds={"indication": 0.9}))
    r.turn("가짜고민 시술")
    r.set_noul(jev({"가짜계열": 0.9, "가짜고민": 0.9}, kinds={"overview": 0.9}))
    row, kinds, body = r.turn("가짜계열 은 어떤 거예요")
    mod = row["list"]["concerns"][0]["modalities"][0]
    assert mod["modality_code"] == "ZZM-1" and mod["interest"] is True
    assert all(p["interest"] is False for p in mod["procedures"])  # 계열은 머리에만(W-3)
    html = _event_html(body, "list")
    assert re.search(r'class="cs-mod" data-modality-code="ZZM-1" data-interest', html)
    assert not re.search(r'data-procedure-code="ZZP-1" data-interest', html)
    r.close()


def test_unchanged_list_not_resent(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=jev(kinds={"indication": 0.9}))
    r.turn("가짜고민 시술")
    r.set_noul(jev({"가짜고민": 0.9}, kinds={"indication": 0.9}))
    row, kinds, _ = r.turn("가짜고민 다시")
    assert "list" not in kinds and row["list"] is None  # SC3 — 바뀐 것이 없으면 안 보낸다
    r.close()


def test_off_list_render_unchanged():
    lst = {"concerns": [{"concern_code": "C", "concern_name": "n", "inherited_from": None,
                         "modalities": [{"modality_name": "m", "procedures": [
                             {"procedure_code": "P", "procedure_name": "p", "reach": ["r"], "concern_match": ["c"],
                              "gids": []}]}], "modality_only": [], "unreached": [], "counts": {}}]}
    out = Render().list(lst)
    assert "data-interest" not in out and "data-synergy" not in out and "cs-interest" not in out
    assert '<div class="cs-mod">' in out and '<li class="cs-proc" data-procedure-code="P">' in out


def _strip(text: str, kind: str) -> str:
    if kind == "py":
        text = re.sub(r'(?s)""".*?"""', "", text)
        return "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())
    return re.sub(r"(?s)\{#.*?#\}", "", text)


def test_s5_t14_no_hangul_in_new_code_and_templates():
    """새 코드 · 새 조각에 한글 문장 0 — 예외는 실측 입력 글(`consult/e5_inputs.json` — 시험 자료 · 화면에 보일 문장 아님)."""
    bad = []
    for name in NEW_CODE:
        p = CONSULT / name
        if p.is_file() and HANGUL.search(_strip(p.read_text(encoding="utf-8"), "py")):
            bad.append(name)
    for name in NEW_TEMPLATES:
        p = CONSULT / "templates" / "partials" / name
        if HANGUL.search(_strip(p.read_text(encoding="utf-8"), "html")):
            bad.append(name)
    assert bad == []
    for name in NEW_TEMPLATES:
        t = (CONSULT / "templates" / "partials" / name).read_text(encoding="utf-8")
        assert "style=" not in t and not re.search(r"\son[a-z]+=", t) and "<script" not in t
