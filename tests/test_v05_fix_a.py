"""로컬 테스트 고침 A(사용자 11-22) — 얼굴 점 = 대화 부위 · 진료항목 ↔ 점 품음 계산 · 점 클릭 거르기 · 고민 → 같은 시술 범위.

외부 호출 0(가짜 허브 · 가짜 CLI · 고정본 `$J` + 이 파일에서 더한 부위 · 진료항목).
1. 점 = 지금 주제 `sites`(말한 부위 + 고민 부위) · 진료항목 부위를 모아 찍지 않음(얼굴 · 전신 대표점 0).
2. 진료항목 ↔ 점 = 매핑 부위가 점 부위를 품음(같음 · 조상) — 얼굴 전체는 얼굴 점 모두 · 전신은 몸 점 모두.
3. 점 클릭 = 그 점에 속하는 진료항목만 · 다른 점은 그대로 · 같은 점 다시 = 전체.
4. 고민 → 케이스 직접 + 같은 시술 · 매핑 부위가 케이스 부위를 품는 진료항목.
5. 카드 강조 자리 = 속한 점 · 6. 다음 턴에 점이 쌓임(장부 `consult.topics[].sites` · 재시작 복원).
"""

from __future__ import annotations

import asyncio
import copy
import json
import re

from fastapi.testclient import TestClient

from clinic_chat.consult import detect as dt
from clinic_chat.consult import items_view as iv
from clinic_chat.consult import kos_map as km
from clinic_chat.consult.render import Render
from clinic_chat.consult.server import create_app
from clinic_chat.consult.turn import Consult
from tests.conftest import KOS_FIXTURE
from tests.fake_hub import FakeHub
from tests.state_fixture import Run, state_hub
from tests.test_v05_detect import EXTRA_ENTRY, understand_out
from tests.v04_fixture import entry_rows, list_route


def kos() -> km.KosMap:
    """고정본 + 볼(ZS-051) ⊃ 팔자주름(ZS-046) · 코(ZS-035) · 필러 셋(얼굴 · 코 · 팔자주름 직접 케이스) · 얼굴 보톡스."""
    d = json.loads(KOS_FIXTURE.read_text(encoding="utf-8"))
    d["sites"]["ZS-051"] = {
        "name": "볼",
        "parent_code": "ZS-007",
        "region": "face",
        "shape": "pair",
        "points": [[0.4, 0.55], [0.6, 0.55]],
    }
    d["sites"]["ZS-046"] = {
        "name": "팔자주름",
        "parent_code": "ZS-051",
        "region": "face",
        "shape": "pair",
        "points": [[0.38, 0.59], [0.62, 0.59]],
    }
    d["sites"]["ZS-035"] = {
        "name": "코",
        "parent_code": "ZS-007",
        "region": "face",
        "shape": "point",
        "points": [[0.5, 0.52]],
    }
    base = copy.deepcopy(d["items"][0])
    new = [
        ("ZZ-FIL-FACE", "가짜 얼굴필러", "가짜 필러", ["ZPRC-100"], ["ZCAS-1046"], ["ZS-007"]),
        ("ZZ-FIL-NOSE", "가짜 코필러", "가짜 필러", ["ZPRC-100"], ["ZCAS-1050"], ["ZS-035"]),
        ("ZZ-FIL-NL", "가짜 팔자필러", "가짜 필러", ["ZPRC-100"], ["ZCAS-1008"], ["ZS-046"]),
        ("ZZ-BTX-FACE", "가짜 얼굴 보톡스", "가짜 보톡스", ["ZPRC-001"], ["ZCAS-099"], ["ZS-007"]),
    ]
    for code, name, proc, procs, cases, sites in new:
        it = copy.deepcopy(base)
        it.update(kos_item_code=code, kos_item_name=name, kos_procedure=proc, option_refs=[])
        it["hub"] = {"procedure_codes": procs, "case_codes": cases, "site_codes": sites, "products": []}
        d["items"].append(it)
    d["groups"][0]["items"].append("ZZ-BTX-FACE")
    d["groups"].append({"kos_procedure": "가짜 필러", "items": ["ZZ-FIL-FACE", "ZZ-FIL-NOSE", "ZZ-FIL-NL"]})
    return km.parse(json.dumps(d, ensure_ascii=False).encode(), expect_items=None)


# ---------- 2 · 품음 계산 ----------


def test_belongs_same_or_ancestor():
    m = kos()
    assert iv.belongs(m, "ZZ-LIFT", "ZS-046") and iv.belongs(m, "ZZ-LIFT", "ZS-072")  # 얼굴 ⊃ 팔자주름 · 턱
    assert iv.belongs(m, "ZZ-LIFT", "ZS-079")  # 전신 ⊃ 목(몸 점)
    assert iv.belongs(m, "ZZ-BTX-A", "ZS-072") and iv.belongs(m, "ZZ-BTX-A", "ZS-073")  # 턱 ⊃ 턱끝
    assert not iv.belongs(m, "ZZ-BTX-A", "ZS-046") and not iv.belongs(m, "ZZ-BTX-A", "ZS-007")  # 자손은 품지 않음
    assert not iv.belongs(m, "ZZ-FIL-NOSE", "ZS-046") and not iv.belongs(m, "ZZ-LIFT", "ZS-없음")


# ---------- 1 · 5 · 점 = 대화 부위 ----------


def test_dots_are_conversation_sites_not_item_sites():
    m = kos()
    v = iv.view(m, ["ZZ-LIFT", "ZZ-BTX-A", "ZZ-FIL-FACE"], sites=["ZS-046"])
    assert [d["site_code"] for d in v["dots"]] == ["ZS-046"] and v[
        "body"
    ] == []  # 얼굴 대표점 0 · 「전신」 0(사용자 본 문제)
    assert v["dots"][0]["points"] == [[224.2, 625.4], [365.8, 625.4]]
    cards = {c["code"]: c for k in v["kinds"] for c in k["cards"]}
    assert cards["ZZ-LIFT"]["points"] == "224.2,625.4;365.8,625.4" and cards["ZZ-LIFT"]["body_sites"] == ""
    assert cards["ZZ-BTX-A"]["points"] == ""  # 턱 진료항목은 팔자주름 점에 속하지 않음
    none = iv.view(m, ["ZZ-LIFT", "ZZ-BTX-A"])  # 대화 부위 0 → 점 0
    assert none["dots"] == [] and none["body"] == []
    body = iv.view(m, ["ZZ-LIFT", "ZZ--"], sites=["ZS-079", "ZS-072"])
    assert [b["site_code"] for b in body["body"]] == ["ZS-079"] and [d["site_code"] for d in body["dots"]] == ["ZS-072"]
    lift = next(c for k in body["kinds"] for c in k["cards"] if c["code"] == "ZZ-LIFT")
    assert lift["body_sites"] == "ZS-079" and lift["points"] == "295.0,636.0"  # 같은 진료항목이 대화에 따라 다른 점에


# ---------- 3 · 점 클릭 ----------


def test_click_filters_and_other_dots_stay():
    m = kos()
    codes = ["ZZ-LIFT", "ZZ-BTX-A", "ZZ-BTX-B", "ZZ-FIL-NOSE"]
    sites = ["ZS-046", "ZS-072", "ZS-057"]
    full = iv.view(m, codes, sites=sites)
    jaw = iv.view(m, codes, sites=sites, site="ZS-072")
    assert full["n_items"] == 4 and [c["code"] for k in jaw["kinds"] for c in k["cards"]] == [
        "ZZ-LIFT",
        "ZZ-BTX-A",
    ]  # 묶음 = 처음 나온 순(IL2)
    assert [d["site_code"] for d in jaw["dots"]] == sites  # 다른 점은 그대로
    nl = iv.view(m, codes, sites=sites, site="ZS-046")
    assert [c["code"] for k in nl["kinds"] for c in k["cards"]] == ["ZZ-LIFT"]
    out = Render().items(jaw, "cid1")
    marks = out[out.index('id="face-marks"') :]
    assert marks.count('<circle class="cs-dot"') == 5  # 팔자 2 + 턱 1 + 입 2
    assert re.search(
        r'data-site-code="ZS-072" data-cs-pick hx-get="/c/cid1/items" [^>]*aria-pressed="true"', marks
    )  # 같은 점 다시 = 전체
    assert re.search(
        r'data-site-code="ZS-046" data-cs-pick hx-get="/c/cid1/items\?site=ZS-046" [^>]*aria-pressed="false"', marks
    )
    assert 'data-site-filter="ZS-072"' in out
    # 코디 11-22 — 누른 점 = 시안 cs-dot__ring 웨이브(누른 점 자리에만 · 다른 점 그대로)
    assert marks.count('class="cs-dot__ring"') == 1 and 'data-ring-for="ZS-072" cx="295.0" cy="636.0" r="16"' in marks
    assert 'data-ring-for="ZS-046"' not in marks and "data-ring-for" not in Render().items(full, "cid1")


def test_items_route_filter_keeps_dots(tmp_path, fake_cli):
    hub = state_hub()
    hub.routes["resolve"] = list_route(entry_rows() + EXTRA_ENTRY + SITE_ENTRY)
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        two_site_turns(r, fake_cli)
        out = r.c.get(f"/c/{r.cid}/items?site=ZS-072").text  # 턱 누름
        assert 'data-code="ZZ-BTX-A"' in out and 'data-code="ZZ-BTX-B"' not in out
        assert 'data-site-code="ZS-072"' in out and 'data-site-code="ZS-057"' in out  # 입 점 남음
        rows = [json.loads(x) for x in (tmp_path / "chat" / "turns.jsonl").read_text(encoding="utf-8").splitlines()]
        assert sum(1 for x in rows if x["phase"] == "end") == 2  # 화면 동작만 — 장부 턴 0
    finally:
        r.close()


# ---------- 4 · 고민 → 같은 시술 범위 ----------


def cc_route(p):
    if p.get("concern_code") != "ZZC-NL":
        return {"groups": [], "modality_only": [], "unreached": []}
    rows = [
        {
            "reach": "케이스 직접",
            "candidate_table": "procedure_case",
            "candidate_code": "ZCAS-1008",
            "procedure_code": "ZPRC-100",
            "site_code": "",
            "case_site_name": "팔자주름",
            "concern_code": "ZZC-NL",
        },
        {
            "reach": "케이스 직접",
            "candidate_table": "procedure_case",
            "candidate_code": "ZCAS-093",
            "procedure_code": "ZPRC-001",
            "site_code": "",
            "case_site_name": "팔자주름",
            "concern_code": "ZZC-NL",
        },
        {
            "reach": "케이스 직접",
            "candidate_table": "procedure_case",
            "candidate_code": "ZCAS-777",
            "procedure_code": "ZPRC-100",
            "site_code": "",
            "case_site_name": "없는부위",
            "concern_code": "ZZC-NL",
        },
    ]
    return {
        "groups": [{"candidate_table": "procedure_case", "rows": rows}],
        "modality_only": [],
        "unreached": [],
        "sites": [{"site_code": "ZS-046", "match": "직접"}, {"site_code": "ZS-051", "match": "하위"}],
    }


def test_concern_adds_same_procedure_scope():
    m = kos()
    d = dt.Detector(m, FakeHub({"concern_candidates": cc_route}).client(page_limit=50))
    r = asyncio.run(d.items({"concern": [{"table": "concern", "code": "ZZC-NL"}]}))
    assert set(r.codes) == {"ZZ-FIL-NL", "ZZ-FIL-FACE", "ZZ-BTX-FACE"}  # 직접 + 얼굴필러 + 얼굴 보톡스 · 코필러 0
    assert r.case_scope["ZCAS-1008"] == {"site": "ZS-046", "added": ["ZZ-FIL-FACE"]}
    assert r.case_scope["ZCAS-093"] == {"site": "ZS-046", "added": ["ZZ-BTX-FACE"]}
    assert "ZCAS-777" not in r.case_scope  # 케이스 부위를 못 찾으면 직접만
    assert d.concern_sites("ZZC-NL") == ["ZS-046"]  # 고민 부위 = 「직접」 만


# ---------- 6 · 다음 턴 쌓임 ----------


def test_add_sites_accumulates_only_kept_values():
    m = kos()
    d = dt.Detector(m, FakeHub({"concern_candidates": cc_route}).client(page_limit=50))
    asyncio.run(d.items({"concern": [{"table": "concern", "code": "ZZC-NL"}]}))
    cur = {"id": "t1", "conditions": {"concern": [{"code": "ZZC-NL"}], "site": [{"code": "ZS-072"}]}, "items": []}
    turn = {"concern": [{"code": "ZZC-NL"}], "site": [{"code": "ZS-072"}, {"code": "ZS-057"}]}  # 입은 막힘(조건 밖)
    assert Consult._add_sites(m, d, cur, turn) == ["ZS-072", "ZS-046"] and cur["sites"] == ["ZS-072", "ZS-046"]
    cur["conditions"]["site"] = [{"code": "ZS-057"}]
    assert Consult._add_sites(m, d, cur, {"site": [{"code": "ZS-057"}]}) == ["ZS-057"]
    assert cur["sites"] == ["ZS-072", "ZS-046", "ZS-057"]  # 앞 점은 남는다
    assert Consult._add_sites(m, d, None, turn) == []


SITE_ENTRY = [
    {"entity_table": "site", "entity_code": "ZS-072", "entity_name": "가짜턱", "name_kind": "정식", "text": "가짜턱"},
    {"entity_table": "site", "entity_code": "ZS-057", "entity_name": "가짜입", "name_kind": "정식", "text": "가짜입"},
]


def two_site_turns(r, fake_cli):
    """주제 튜플(11-22) 뒤 — 1 턴 시술만(점 0) → 2 턴 부위 둘(빈 부위 칸을 채움 = 깊게 · 같은 주제)."""
    (fake_cli[1] / "understand.json").write_text(understand_out([("procedure", "ZPRC-001")]), encoding="utf-8")
    row1, _, body1 = r.turn("가짜보톡스시술")
    (fake_cli[1] / "understand.json").write_text(
        understand_out([("site", "ZS-072"), ("site", "ZS-057")]), encoding="utf-8"
    )
    row2, kinds2, body2 = r.turn("가짜턱 가짜입")
    return row1, body1, row2, kinds2, body2


def test_turns_accumulate_dots_ledger_restart(tmp_path, fake_cli):
    hub = state_hub()
    hub.routes["resolve"] = list_route(entry_rows() + EXTRA_ENTRY + SITE_ENTRY)
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        row1, body1, row2, kinds2, body2 = two_site_turns(r, fake_cli)
        t1 = row1["consult"]["topics"][0]
        assert t1["sites"] == [] and row1["items"]["sites_added"] == []
        assert "data-site-code=" not in body1.split('id="face-marks"')[-1]  # 대화 부위 0 → 점 0(얼굴 대표점 · 전신 0)
        c2 = row2["consult"]
        assert c2["active_topic"] == "t1" and c2["topics"][0]["sites"] == ["ZS-072", "ZS-057"]  # 다음 턴 쌓임
        assert row2["items"]["codes"] == ["ZZ-BTX-A", "ZZ-BTX-B"] and ">전신<" not in body2
        assert "items" in kinds2 and 'data-site-code="ZS-057"' in body2 and 'data-site-code="ZS-072"' in body2
        assert "data-site-filter" not in body2  # 새 턴 목록 = 선택 풂
    finally:
        r.close()
    page = TestClient(create_app(r.s)).get(f"/c/{r.cid}").text  # 재시작 — 주제 `sites` 로 되살림
    marks = page[page.index('id="face-marks"') :]
    assert 'data-site-code="ZS-072"' in marks and 'data-site-code="ZS-057"' in marks


# ---------- 테스트 고침 D(11-23) — 얼굴 점은 늘 다 · 점 = 필터(앞 주제 점이면 그 주제) · 정적 파일 판 표지 ----------


def test_fixd_dots_all_topics_current_marked():
    m = kos()
    topics = [
        {"id": "t1", "conditions": {}, "items": ["ZZ-BTX-A", "ZZ-LIFT"], "created_turn": 1, "sites": ["ZS-072"]},
        {"id": "t2", "conditions": {}, "items": ["ZZ-BTX-B"], "created_turn": 2, "sites": ["ZS-057", "ZS-079"]},
    ]
    v = iv.topics_view(m, topics, "t2")
    assert [(d["site_code"], d["topic"], d["current"]) for d in v["dots"]] == [
        ("ZS-072", "t1", False),
        ("ZS-057", "t2", True),
    ]  # 앞 주제 점도 뜸 · 지금 주제 점만 강조
    assert [(b["site_code"], b["current"]) for b in v["body"]] == [("ZS-079", True)]
    out = Render().items(v, "cid")
    assert 'class="cs-dot cs-dot--other" data-site-code="ZS-072" data-topic="t1"' in out
    assert 'class="cs-dot" data-site-code="ZS-057" data-topic="t2"' in out
    assert iv.site_owner(topics, "t2", "ZS-072") == "t1" and iv.site_owner(topics, "t2", "ZS-057") == "t2"
    assert iv.site_owner(topics, "t2", "ZS-999") is None


def test_fixd_prev_topic_dot_filters_that_topic_no_ledger(tmp_path, fake_cli):
    hub = state_hub()
    hub.routes["resolve"] = list_route(entry_rows() + EXTRA_ENTRY + SITE_ENTRY)
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        for text, tg in (("가짜턱", [("site", "ZS-072")]), ("가짜입", [("site", "ZS-057")])):
            (fake_cli[1] / "understand.json").write_text(understand_out(tg), encoding="utf-8")
            row, kinds, body = r.turn(text)
        assert row["consult"]["active_topic"] == "t2"
        marks = body[body.index('id="face-marks"') :]
        assert 'data-site-code="ZS-072"' in marks and 'data-site-code="ZS-057"' in marks  # 주제 바뀜에도 점 둘 다
        n_rows = len((tmp_path / "chat" / "turns.jsonl").read_text(encoding="utf-8").splitlines())
        out = r.c.get(f"/c/{r.cid}/items?site=ZS-072").text  # 앞 주제(t1) 점 누름
        assert re.search(r'data-topic-id="t1" data-topic-current open', out)  # 그 주제 조건 목록
        assert 'data-code="ZZ-BTX-A"' in out and 'data-code="ZZ-BTX-B"' not in out
        mk = out[out.index('id="face-marks"') :]
        assert 'data-ring-for="ZS-072"' in mk and 'data-site-code="ZS-057"' in mk  # 누른 점 웨이브 · 다른 점 그대로
        assert len((tmp_path / "chat" / "turns.jsonl").read_text(encoding="utf-8").splitlines()) == n_rows  # 장부 0
        back = r.c.get(f"/c/{r.cid}/items").text  # 같은 점 다시 = 전체(대화 주제)
        assert re.search(r'data-topic-id="t2" data-topic-current open', back)
    finally:
        r.close()


def test_fixd_static_assets_versioned(tmp_path, fake_cli):
    from clinic_chat.consult.render import asset

    r = Run(tmp_path, fake_cli)
    try:
        page = r.c.get(f"/c/{r.cid}").text
    finally:
        r.close()
    for name in ("consult.css", "consult.js", "medikit.css", "medikit.js"):
        assert re.search(rf'/static/{re.escape(name)}\?v=[0-9a-f]{{10}}"', page), name
    assert asset("consult.css") in page and asset("nope.css") == "/static/nope.css"
