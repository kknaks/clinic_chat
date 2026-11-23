"""SPEC-010 WP-4 — 시술 설명 모달 값(§4-5 · MD1 ~ MD5 · §8 IM5 ~ IM12). 외부 호출 0(가짜 허브 · 고정본 `$J` · 임시 그림 폴더)."""

from __future__ import annotations

import re
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from clinic_chat.columns.base import Deps
from clinic_chat.consult import kos_map as km
from clinic_chat.consult import modal as modal_mod
from clinic_chat.consult.fake_turn import make
from clinic_chat.consult.server import create_app
from tests.conftest import KOS_FIXTURE, KOS_FIXTURE_ITEMS, make_settings
from tests.fake_hub import FakeHub

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>'


@pytest.fixture(autouse=True)
def _clear():
    modal_mod.clear_cache()
    yield
    modal_mod.clear_cache()


def proc_row(code, mec, text, overview):
    return {
        "subject_table": "procedure",
        "subject_code": code,
        "overview": overview,
        "mechanism_code": mec,
        "mechanism_name": "mn-" + code,
        "mechanism_text": text,
        "layer": {"role": "물려받음", "holder": {"holder_table": "procedure", "holder_code": code}},
    }


def case_row(code, mcodes, mtexts):
    return {
        "subject_table": "procedure_case",
        "subject_code": code,
        "overview": "",
        "mechanism_text": "",
        "method_codes": mcodes,
        "method_texts": mtexts,
        "layer": {"role": "자기", "holder": {"holder_table": "procedure_case", "holder_code": code}},
    }


MOD = {
    "subject_table": "modality",
    "subject_code": "ZM-1",
    "overview": "계열 개요",
    "layer": {"role": "물려받음", "holder": {"holder_table": "modality", "holder_code": "ZM-1"}},
}
PROFILE = {
    "procedure_case:ZCAS-001": [
        case_row("ZCAS-001", "MET-002 · MET-004", "방법 둘 글\n방법 넷 글 · 가운뎃점"),
        proc_row("ZPRC-001", "MEC-001", "원리 하나 글", "개요 하나"),
        MOD,
    ],
    "procedure_case:ZCAS-010": [
        case_row("ZCAS-010", "MET-002 · MET-006", "둘\n여섯"),
        proc_row("ZPRC-010", "MEC-050", "원리 오십", "개요 십"),
        MOD,
    ],
    "procedure_case:ZCAS-011": [
        case_row("ZCAS-011", "MET-007", "일곱"),
        proc_row("ZPRC-010", "MEC-050", "원리 오십", "개요 십"),
    ],
    "procedure_case:ZCAS-020": [case_row("ZCAS-020", "", ""), proc_row("ZPRC-020", "", "", "개요 이십")],
}
OUTCOME = {
    "procedure_case:ZCAS-010": [
        {"effect_code": "ZE-1", "effect_name": "효과일", "note": "메모일"},
        {"effect_code": "", "onset_text": "2일부터"},
        {"effect_code": "ZE-2", "effect_name": "효과이", "note": ""},
    ],
    "procedure_case:ZCAS-011": [
        {"effect_code": "ZE-2", "effect_name": "효과이(둘째)", "note": ""},
        {"effect_code": "ZE-3", "effect_name": "효과삼", "note": ""},
    ],
}
ADVERSE = {
    "procedure_case:ZCAS-010": [
        {"adverse_code": "ZA-1", "adverse_name": "멍", "severity_text": "가벼움", "note": "n", "polarity": "발생함"},
        {"adverse_code": "ZA-9", "adverse_name": "없음줄", "polarity": "보고 없음"},
    ],
    "procedure_case:ZCAS-011": [{"adverse_code": "ZA-1", "adverse_name": "멍(둘째)", "polarity": "발생함"}],
}


def facets(p):
    node, f = p.get("node"), p.get("facet")
    table = {"profile": PROFILE, "outcome": OUTCOME, "adverse": ADVERSE}.get(f, {})
    return {"rows": table.get(node, [])}


def seed(tmp_path):
    d = tmp_path / "images"
    (d / "mechanism").mkdir(parents=True)
    (d / "method").mkdir()
    for f in (
        "mechanism/MEC-001.svg",
        "mechanism/MEC-050.svg",
        "method/MET-002.svg",
        "method/MET-006.svg",
        "method/MET-007.svg",
    ):
        (d / f).write_bytes(SVG)
    return d  # MET-004 없음(IM8)


def app(tmp_path, hub: FakeHub | None = None, image_dir="seed"):
    h = hub or FakeHub({"facets": facets})
    d = seed(tmp_path) if image_dir == "seed" else image_dir
    s = make_settings(
        tmp_path, chat_dir=str(tmp_path / "chat"), chat_fake_turn=True, chat_image_dir=str(d) if d else None
    )
    a = create_app(s, turn_fn=make(0), deps_factory=lambda st: Deps(st, h.client(page_limit=50), None))
    return TestClient(a), h, a


def open_(c, code, cid="mdconv0000000001"):
    c.get(f"/c/{cid}")
    return c.get(f"/c/{cid}/item?code={quote(code, safe='')}")


def figs(html):
    return re.findall(r'data-pm-fig="(\w+)" data-code="([^"]*)"', html)


def test_one_case_fields_figures_order(tmp_path):
    c, _, _ = app(tmp_path)
    r = open_(c, "ZZ-BTX-A")
    h = r.text
    assert r.status_code == 200 and 'data-pm="name">가짜 턱 보톡스 A<' in h and 'data-pm="kind">가짜 보톡스<' in h
    for k, v in (
        ("code", "ZZ-BTX-A"),
        ("kind2", "가짜 보톡스"),
        ("site", "턱"),
        ("drug", "가짜톡스"),
        ("device", "해당 없음"),
        ("lead", "개요 하나"),
    ):
        assert f'data-pm="{k}">{v}<' in h, k  # $J 원문 · lead = 물려받은 시술 행(N5 · 계열 행 아님)
    assert figs(h) == [("mechanism", "MEC-001"), ("method", "MET-002"), ("method", "MET-004")]  # IM5 · IM6 순서
    assert 'data-pm="mecText">원리 하나 글<' in h and 'data-pm="metText">방법 넷 글 · 가운뎃점<' in h  # WH2 줄바꿈 나눔
    assert 'src="/img/mechanism/MEC-001.svg"' in h and 'src="/img/method/MET-002.svg"' in h
    met4 = h.split('data-code="MET-004"')[1].split("</figure>")[0]
    assert "<img" not in met4 and 'data-slot="pm-fig-missing"' in met4  # IM8 — 파일 없음
    assert 'alt=""' in h  # IM10 빈 slot
    assert h.count('data-slot="pm-empty" hidden') == 1  # 허브 칸 있음 → 빈 자리 숨김


def test_md1_multi_case_merge(tmp_path):
    c, _, _ = app(tmp_path)
    h = open_(c, "ZZ-LIFT").text
    assert figs(h) == [("mechanism", "MEC-050"), ("method", "MET-002"), ("method", "MET-006"), ("method", "MET-007")]
    eff = re.findall(r'data-pm-list="effect">(.*?)</ul>', h, re.S)[0]
    assert re.findall(r'data-case="(ZCAS-\d+)" data-code="(ZE-\d)"', eff) == [
        ("ZCAS-010", "ZE-1"),
        ("ZCAS-010", "ZE-2"),
        ("ZCAS-011", "ZE-2"),
        ("ZCAS-011", "ZE-3"),
    ]  # 11-22 — 같은 이름이 하나(코드가 같아도 이름이 다르면 따로)
    assert "2일부터" not in eff and "메모일" not in eff  # N3 경과 줄 뺌 · 메모는 모달 칸 아님(빈도 · 언제 · 얼마나만)
    side = re.findall(r'data-pm-list="side">(.*?)</ul>', h, re.S)[0]
    assert re.findall(r'data-code="(ZA-\d)"', side) == ["ZA-1", "ZA-1"] and "없음줄" not in side  # N4 발생함만
    assert (
        '<span class="cs-pm__f" data-f="severity"><span class="cs-pm__fk" data-slot="pm-f-severity"></span>가벼움</span>'
        in side
    )


def test_md2_no_case_hides_hub_fields(tmp_path):
    c, h_, _ = app(tmp_path)
    h = open_(c, "ZZ IV DRIP + VIT").text
    assert 'data-pm="name">가짜 수액<' in h and "data-pm-fig" not in h and "<li" not in h
    assert h.count('data-slot="pm-empty"') == 3 and 'data-slot="pm-empty" hidden' not in h
    assert not [t for t, _ in h_.requests if t == "facets"]  # 허브 호출 0
    for tab in ("시술 개요", "설명", "효과", "부작용"):
        assert f">{tab}</a>" in h


def test_im9_no_mechanism_code_hides_figure(tmp_path):
    c, _, _ = app(tmp_path)
    h = open_(c, "ZZ--").text
    assert figs(h) == [] and 'data-pm="lead">개요 이십<' in h


def test_md3_hub_error_and_retry(tmp_path):
    hub = FakeHub({"facets": lambda p: (500, {"detail": "internal: fixture"})})
    c, _, a = app(tmp_path, hub)
    h = open_(c, "ZZ-BTX-A").text
    assert "data-pm-fig" not in h and h.count('data-slot="pm-empty"') == 3
    rows = a.state.ctx.ledger.rows("mdconv0000000001")
    assert rows[-1]["phase"] == "viewed" and rows[-1]["modal"]["error"]["kind"]
    hub.routes["facets"] = facets  # 다시 열면 다시 부른다(오류는 캐시 안 함)
    assert figs(open_(c, "ZZ-BTX-A").text)


def test_cache_and_viewed_ledger(tmp_path):
    c, hub, a = app(tmp_path)
    open_(c, "ZZ-BTX-A")
    n = len(hub.requests)
    open_(c, "ZZ-BTX-A")
    assert len(hub.requests) == n  # 프로세스 캐시 (gold_hash, 노드, facet)
    rows = [r for r in a.state.ctx.ledger.rows("mdconv0000000001") if r["phase"] == "viewed"]
    assert rows[-1]["code"] == "ZZ-BTX-A" and rows[-1]["modal"]["figs"] == [
        {"table": "mechanism", "code": "MEC-001", "present": True},
        {"table": "method", "code": "MET-002", "present": True},
        {"table": "method", "code": "MET-004", "present": False},
    ]  # IM12
    raw = (tmp_path / "chat" / "turns.jsonl").read_text(encoding="utf-8")
    assert "price_amount" not in raw  # MD5 · JS6


def test_empty_image_dir_no_img(tmp_path):
    c, _, _ = app(tmp_path, image_dir=None)
    h = open_(c, "ZZ-BTX-A").text
    assert "<img" not in h and figs(h)  # 그림 칸 숨김(IM2) · 태그 · 글은 남음


def test_rt1_codes_and_unknown(tmp_path):
    c, _, _ = app(tmp_path)
    for code in ("ZZ IV DRIP + VIT", "ZZ-시트", "ZZ--"):
        assert open_(c, code).status_code == 200, code
    assert open_(c, "ZZ-NONE").status_code == 404
    assert c.get("/c/x/item").status_code == 422


def test_page_still_empty_frame(tmp_path):
    c, _, _ = app(tmp_path)
    full = c.get("/").text
    frame = full.split('<dialog id="cs-proc-modal"')[1]
    assert 'data-pm="mecText"' in frame and 'data-pm="metText"' in frame and "<img" not in frame  # 빈 틀(WP-2)


# ---------- 11-22 모달 효과 · 부작용 정리 ----------

MODL = {"role": "물려받음", "holder": {"holder_table": "modality", "holder_code": "ZM-1"}}
PROC = {"role": "물려받음", "holder": {"holder_table": "procedure", "holder_code": "ZPRC-010"}}
OWN = {"role": "자기", "holder": {"holder_table": "procedure_case", "holder_code": "ZCAS-010"}}


def test_tidy_merge_drop_modality_classes_alerts():
    eff = [
        {
            "effect_code": "ZE-1",
            "effect_name": "볼륨",
            "onset_text": "즉시",
            "duration_text": "6~12개월",
            "layer": PROC,
            "_case": "A",
        },
        {
            "effect_code": "ZE-1",
            "effect_name": "볼륨",
            "duration_text": "6~12개월",
            "peak_text": "2주",
            "layer": OWN,
            "_case": "B",
        },
        {"effect_code": "ZE-9", "effect_name": "계열효과", "duration_text": "x", "layer": MODL, "_case": "A"},
    ]
    e = modal_mod.effects(eff)
    assert [x["name"] for x in e] == ["볼륨"]  # 계열 줄 뺌 · 같은 이름 하나
    assert [(p["f"], p["v"]) for p in e[0]["parts"]] == [("onset", "즉시"), ("peak", "2주"), ("duration", "6~12개월")]
    side = [
        {
            "adverse_code": "ZA-1",
            "adverse_name": "멍",
            "severity_class": "일시적",
            "freq_grade_text": "흔함",
            "duration_text": "1~2주",
            "guidance_texts": "냉찜질\n아스피린 피하기\n심한 멍이 계속 커지면",
            "guidance_kinds": "관리\n예방\n경고 기준",
            "guidance_alerts": "\n\ntrue",
            "layer": PROC,
            "_case": "A",
        },
        {
            "adverse_code": "ZA-1",
            "adverse_name": "멍",
            "severity_class": "일시적",
            "freq_grade_text": "흔함",
            "duration_text": "3일",
            "guidance_texts": "냉찜질",
            "guidance_kinds": "관리",
            "guidance_alerts": "",
            "layer": OWN,
            "_case": "B",
        },
        {
            "adverse_code": "ZA-5",
            "adverse_name": "혈관 폐색",
            "severity_class": "위급",
            "guidance_texts": "즉시 내원",
            "guidance_kinds": "관리",
            "guidance_alerts": "",
            "layer": PROC,
            "_case": "A",
        },
        {"adverse_code": "ZA-7", "adverse_name": "근력 저하", "severity_class": "주의", "layer": MODL, "_case": "A"},
        {"adverse_code": "", "adverse_name": "기타", "severity_class": "", "layer": OWN, "_case": "B"},
    ]
    s = modal_mod.sides(side)
    assert [x["name"] for x in s] == ["멍", "혈관 폐색", "기타"]  # 계열 「근력 저하」 뺌 · 멍 3 줄 → 하나
    m = s[0]
    assert [(p["f"], p["v"]) for p in m["parts"]] == [
        ("freq", "흔함"),
        ("duration", "1~2주"),
        ("duration", "3일"),
        ("care", "냉찜질"),
    ]
    assert m["alerts"] == ["심한 멍이 계속 커지면"] and "아스피린 피하기" not in str(m["parts"])  # 예방 = 모달 0
    assert (
        s[1]["parts"] == [{"f": "care", "v": "즉시 내원"}] and s[2]["parts"] == []
    )  # 값 없는 칸은 비움(지어 넣지 않음)
    g = modal_mod.side_groups(s)
    assert [(x["key"], [r["name"] for r in x["rows"]]) for x in g] == [
        ("urgent", ["혈관 폐색"]),
        ("temporary", ["멍"]),
        ("none", ["기타"]),
    ]  # 위급 맨 위 · 빈 묶음 뺌


def test_tidy_markup_groups_and_alert(tmp_path):
    side = {
        "procedure_case:ZCAS-010": [
            {
                "adverse_code": "ZA-5",
                "adverse_name": "혈관 폐색",
                "severity_class": "위급",
                "polarity": "발생함",
                "guidance_texts": "즉시 내원\n피부가 하얗게 변하면",
                "guidance_kinds": "경고 기준\n경고 기준",
                "guidance_alerts": "\ntrue",
                "layer": PROC,
            },
            {
                "adverse_code": "ZA-1",
                "adverse_name": "멍",
                "severity_class": "일시적",
                "polarity": "발생함",
                "layer": OWN,
            },
            {
                "adverse_code": "ZA-7",
                "adverse_name": "계열부작용",
                "severity_class": "주의",
                "polarity": "발생함",
                "layer": MODL,
            },
        ]
    }

    def f2(p):
        if p.get("facet") == "adverse":
            return {"rows": side.get(p.get("node"), [])}
        return facets(p)

    c, _, _ = app(tmp_path, hub=FakeHub({"facets": f2}))
    h = open_(c, "ZZ-LIFT").text
    sec = h[h.index('id="pm-side"') :]
    assert sec.index("cs-pm__sgrp--urgent") < sec.index("cs-pm__sgrp--temporary") and "cs-pm__sgrp--caution" not in sec
    assert 'data-slot="pm-side-urgent"' in sec and "계열부작용" not in sec
    assert (
        '<div class="cs-pm__alert" role="note"><span class="cs-pm__alert-h" data-slot="pm-alert-head"></span><span class="cs-pm__alert-t">피부가 하얗게 변하면</span></div>'
        in sec
    )


# ---------- 테스트 고침 B — 미리 만든 고객 문장 · 카드 용량 · 설명 ----------

MT_DOC = {
    "meta": {"hub_gold_hash": "z" * 64},
    "cases": {
        "ZCAS-010": {
            "desc_line": "가짜리프팅 · 얼굴 · 집속 초음파",
            "items": [
                {
                    "name": "리프팅",
                    "class": "효과",
                    "when": "즉시",
                    "how_long": "6~12개월",
                    "frequency": "",
                    "what_to_do": "",
                    "alert": "",
                    "sentence": "리프팅 효과를 바로 느낄 수 있고 6~12개월 이어져요.",
                },
                {
                    "name": "화상",
                    "class": "위급",
                    "when": "",
                    "how_long": "",
                    "frequency": "",
                    "what_to_do": "",
                    "alert": "수포가 생기면",
                    "sentence": "화상이 생길 수 있어요.",
                },
                {
                    "name": "부종",
                    "class": "일시적",
                    "when": "",
                    "how_long": "1~3일",
                    "frequency": "",
                    "what_to_do": "",
                    "alert": "",
                    "sentence": "",
                },
            ],
        },
        "ZCAS-011": {
            "desc_line": "",
            "items": [
                {
                    "name": "부종",
                    "class": "일시적",
                    "when": "",
                    "how_long": "2일",
                    "frequency": "",
                    "what_to_do": "",
                    "alert": "",
                    "sentence": "둘째 케이스 부종",
                },
                {
                    "name": "통증",
                    "class": "일시적",
                    "when": "",
                    "how_long": "",
                    "frequency": "정도 4/5",
                    "what_to_do": "",
                    "alert": "",
                    "sentence": "통증이 있을 수 있어요.",
                },
            ],
        },
    },
}


@pytest.fixture
def mt_file():
    from clinic_chat.consult import modal_text as mt

    m = km.load(KOS_FIXTURE, expect_items=KOS_FIXTURE_ITEMS)
    mt.clear_cache()
    mt._CACHE[str(mt.path_for(m))] = MT_DOC
    yield m
    mt.clear_cache()


def test_fixb_modal_text_view_merge_and_groups(mt_file):
    from clinic_chat.consult import modal_text as mt

    v = mt.view(mt_file, "ZZ-LIFT")
    assert v["effects"] == [
        {"name": "리프팅", "class": "효과", "sentence": "리프팅 효과를 바로 느낄 수 있고 6~12개월 이어져요."}
    ]
    assert [(g["cls"], [r["name"] for r in g["rows"]]) for g in v["groups"]] == [
        ("위급", ["화상"]),
        ("일시적", ["부종", "통증"]),
    ]
    bu = v["groups"][1]["rows"][0]
    assert (
        set(bu) == {"name", "class", "sentence"} and bu["sentence"] == ""
    )  # 문장 하나만 · 같은 (분류, 이름)은 먼저 케이스
    assert mt.view(mt_file, "ZZ--") is None  # 파일에 케이스 없음 → 원문 꼴
    assert mt.desc_for(mt_file, "ZZ-LIFT") == "가짜리프팅 · 얼굴 · 집속 초음파" and mt.desc_for(mt_file, "ZZ--") == ""


def test_fixb_modal_markup_text_and_fallback(tmp_path, mt_file):
    c, _, _ = app(tmp_path)
    h = open_(c, "ZZ-LIFT").text
    eff = h[h.index('id="pm-effect"') : h.index('id="pm-side"')]
    assert (
        "data-pm-text" in eff and '<p class="cs-pm__sent">리프팅 효과를 바로 느낄 수 있고 6~12개월 이어져요.</p>' in eff
    )
    assert "cs-pm__chip" not in h and "효과일" not in eff  # 테스트 고침 C · D — 원문 칩 0 · 허브 원문 줄 대신 문장
    side = h[h.index('id="pm-side"') :]
    assert side.index(">위급</p>") < side.index(">일시적</p>")  # 묶음 머리 보임 · 위급 맨 위
    assert "cs-pm__alert" not in side and "수포가 생기면" not in side  # 경고 줄 0(위급 문장이 병원 말을 담음)
    assert '<p class="cs-pm__sent"></p>' not in h and "<li></li>" not in h  # 빈 줄 0
    assert 'data-pm="desc">가짜리프팅 · 얼굴 · 집속 초음파<' in h
    old = open_(c, "ZZ--").text  # 파일에 없는 케이스 = 지금 모달(원문)
    assert "data-pm-text" not in old


def test_fixb_card_unit_and_desc(mt_file):
    from clinic_chat.consult import items_view as iv
    from clinic_chat.consult.render import Render

    v = iv.view(mt_file, ["ZZ-LIFT", "ZZ-BTX-A"])
    lift = next(cd for k in v["kinds"] for cd in k["cards"] if cd["code"] == "ZZ-LIFT")
    assert lift["desc"] == "가짜리프팅 · 얼굴 · 집속 초음파" and lift["unit"] == ""
    it = dict(mt_file.item["ZZ-BTX-A"], kos_unit="10pulse", kos_description="원문 설명")
    cd = iv.card(it, desc="원문 설명")
    assert cd["unit"] == "10pulse" and cd["desc"] == "원문 설명"
    out = Render().items(
        {
            "kinds": [{"name": "k", "cards": [{**cd, "points": "", "body_sites": ""}]}],
            "n_kinds": 1,
            "n_items": 1,
            "dots": [],
            "body": [],
            "site": "",
        },
        "cid",
    )
    assert (
        '<span class="cs-pcard__unit" data-unit>10pulse</span>' in out
        and '<p class="cs-pcard__desc" data-desc>원문 설명</p>' in out
    )


def _builder():
    import importlib.util

    from clinic_chat.config import ROOT

    spec = importlib.util.spec_from_file_location("build_modal_text", ROOT / "scripts" / "build_modal_text.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_fixb_builder_check_guards_and_grades():
    """테스트 고침 C · D — 출력 = {name, class, sentence} · 분류 덮기 · 지어낸 숫자 버림 · 두 문장 넘음 · 숫자 둘 · 위급 병원 말 없음 기록."""
    b = _builder()
    p = {"grades": {"very_low": "매우 드묾", "low": "드묾"}, "stars": "정도 {n}/5"}
    assert b.grade("very_low", p) == "매우 드묾" and b.grade("★★★★☆", p) == "정도 4/5" and b.grade("1.5%", p) == "1.5%"
    side = {"freq": [], "severity": [], "onset": [], "duration": [], "care": [], "alerts": []}
    inp = {
        "effects": [{"name": "리프팅", "onset": ["즉시"], "peak": ["3개월"], "duration": ["6~12개월"]}],
        "sides": [
            {**side, "name": "멍", "class": "일시적", "duration": ["3일"]},
            {**side, "name": "화상", "class": "위급"},
            {**side, "name": "부종", "class": "일시적"},
            {**side, "name": "통증", "class": "일시적"},
        ],
    }
    out = {
        "items": [
            {"name": "리프팅", "class": "주의", "sentence": "바로 느껴지고 6~12개월 가요.", "how_long": "x"},
            {"name": "멍", "class": "일시적", "sentence": "멍은 7일 가요."},
            {"name": "화상", "class": "위급", "sentence": "화상이 생길 수 있어요."},
            {"name": "부종", "class": "일시적", "sentence": "붓기가 생겨요. 금방 빠져요. 걱정 마세요."},
            {"name": "통증", "class": "일시적", "sentence": "3개월 뒤 6~12개월 아파요."},
        ]
    }
    items, bad = b.check(inp, out)
    assert items[0] == {"name": "리프팅", "class": "효과", "sentence": "바로 느껴지고 6~12개월 가요."}  # 칸 = 문장 하나
    assert items[1]["sentence"] == ""  # 7 = 허브에 없는 숫자
    why = {(x["name"], x["why"]) for x in bad}
    assert why == {
        ("멍", "number_not_in_hub"),
        ("화상", "urgent_no_hospital"),
        ("부종", "too_long"),
        ("통증", "many_numbers"),
    }
    assert b.sentences("바로 느껴지고 6~12개월 가요.") == 1 and b.sentences("가. 나. 다") == 3
