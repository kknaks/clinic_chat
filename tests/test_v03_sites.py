"""0.3 WP-7 — 점 누적 · 누르기 · `viewing`(SPEC-006 §7 FD1 ~ FD7 · FD9 ~ FD11 · S6-T17 ~ T19 · 코디 W-1 · W-2 · B-2 · B-4)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from clinic_chat.consult.render import TEMPLATES
from clinic_chat.consult.sites import place
from tests.state_fixture import Run, jev, state_hub

ROOT = Path(__file__).resolve().parent.parent
FACE_SHA = {"clinic_chat/consult/templates/_face.svg": "6337a357a3b9eff57e57b9dfede839f44b2ddc33",
            "clinic_chat/consult/face_coords.py": "669574aa1df52bcbb229b9cba73f547d260dcf3d"}  # WP-0 때 뜬 sha1(shasum)
SUB = "하위"  # 허브 `match` 값 「하위」
DIRECT = "직접"  # 「직접」
SITES = {"ZZC-1": [{"site_code": "SITE-014", "site_name": "가짜이마", "concern_code": "ZZC-1", "csi_id": "CSI-1",
                    "match": DIRECT}],
         "ZZC-2": [{"site_code": "SITE-046", "site_name": "가짜볼", "concern_code": "ZZC-3", "csi_id": "CSI-3",
                    "match": SUB}]}


@pytest.fixture
def run(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, hub=state_hub(sites=SITES), noul=jev(kinds={"indication": 0.9}), chat_ask_max=0)
    yield r
    r.close()


def sites_frag(body: str) -> str:
    blk = body.split("event: sites\n", 1)[1].split("\n\n", 1)[0]
    return "\n".join(ln[6:] for ln in blk.splitlines() if ln.startswith("data: "))


def dots(row):
    return {d["site_code"]: d for d in row["sites"]["dots"]}


# ───── S6-T17 점 누적 ─────

def test_s6_t17_union_kept_and_withdraw_removes(run):
    row1, k1, b1 = run.turn("가짜고민 시술")
    assert list(dots(row1)) == ["SITE-014"] and dots(row1)["SITE-014"]["focus"] is True
    row2, k2, b2 = run.turn("가짜윗고민 은요")
    d2 = dots(row2)
    assert set(d2) == {"SITE-014", "SITE-046"}  # 합집합(FD1)
    assert d2["SITE-046"]["concern_codes"] == ["ZZC-3"] and d2["SITE-046"]["asked_codes"] == ["ZZC-2"]
    assert d2["SITE-046"]["match"] == [SUB] and d2["SITE-046"]["focus"] and not d2["SITE-014"]["focus"]
    frag = sites_frag(b2)
    assert 'data-concern-codes="ZZC-1"' in frag and 'data-concern-codes="ZZC-3"' in frag and "data-focus" in frag
    assert f'hx-get="/c/{run.cid}/list?concern=ZZC-3"' in frag and 'hx-target="#procedure-list"' in frag
    row3, k3, _ = run.turn("가짜팔자 도요")  # 부위 없는 고민 — 앞 점은 남는다(FD3)
    assert row3["sites"] is None or set(dots(row3)) == {"SITE-014", "SITE-046"}
    assert row3["consult"]["after"]["focus"] == "ZZC-9"
    run.set_noul(jev(kinds={"indication": 0.9}, retract={"가짜윗고민": 0.9}))
    row4, _, _ = run.turn("가짜윗고민 은 됐어요")
    assert set(dots(row4)) == {"SITE-014"}  # 거둔 고민의 점만 빠진다


def test_s6_t17_place_focus_and_old_shape():
    pairs = [{"concern_code": "C1", "site_code": "SITE-014", "site_name": "a", "csi_id": "1", "asked": "C1",
              "match": DIRECT},
             {"concern_code": "C3", "site_code": "SITE-007", "site_name": "c", "csi_id": "2", "asked": "C2",
              "match": SUB}]
    s = place(pairs, "C2")
    assert s["dots"][0]["focus"] is False and s["labels"][0]["focus"] is True
    assert s["labels"][0]["concern_codes"] == ["C3"] and s["labels"][0]["asked_codes"] == ["C2"]
    old = place([{k: v for k, v in p.items() if k not in ("asked", "match")} for p in pairs])
    assert "focus" not in old["dots"][0] and "concern_codes" not in old["labels"][0]  # 0.1 꼴 그대로


# ───── S6-T18 누르기 ─────

def test_s6_t18_click_route_memo_no_jev_llm(run):
    run.turn("가짜고민 시술")
    run.turn("가짜윗고민 은요")
    nj, nh = len(run.fj.requests), len(run.hub.requests)
    llm_before = (run.log.read_text(encoding="utf-8") if run.log.exists() else "")
    r = run.c.get(f"/c/{run.cid}/list?concern=ZZC-1")
    assert r.status_code == 200 and 'id="procedure-list"' in r.text and 'data-concern-code="ZZC-1"' in r.text
    assert len(run.fj.requests) == nj and len(run.hub.requests) == nh  # 메모에 있으면 허브 0 · Jev 0
    assert (run.log.read_text(encoding="utf-8") if run.log.exists() else "") == llm_before  # LLM 0
    rows_before = len(run.rows())
    r2 = run.c.get(f"/c/{run.cid}/list?concern=ZZC-3")  # 하위 고민(W-1) — 메모에 없으면 QY1 꼴 조회
    assert r2.status_code == 200 and len(run.hub.requests) > nh and len(run.fj.requests) == nj
    assert len(run.rows()) == rows_before  # 장부 줄 0
    r3 = run.c.get(f"/c/{run.cid}/list?concern=ZZC-3")
    assert r3.text == r2.text  # 메모에 넣었다(N-6)
    bad = run.c.get(f"/c/{run.cid}/list?concern=ZZC-404")
    assert bad.status_code == 422 and 'data-error="view_rejected"' in bad.text
    two = run.c.get(f"/c/{run.cid}/list?concern=ZZC-2&concern=ZZC-1")  # 고민마다 차례로(FD5)
    assert two.text.index('data-concern-code="ZZC-2"') < two.text.index('data-concern-code="ZZC-1"')


# ───── S6-T19 viewing ─────

def _post(run, text, viewing):
    run.n += 1
    run.c.post(f"/c/{run.cid}/turns", data={"text": text, "viewing": viewing})
    body = run.c.get(f"/c/{run.cid}/turns/{run.n}/events").text
    return run.rows()[-1], body


def test_s6_t19_viewing_moves_focus(run):
    run.turn("가짜고민 시술")
    run.turn("가짜윗고민 은요")
    row, _ = _post(run, "이 중에 뭐가 좋아요", "ZZC-1")
    c = row["consult"]
    assert c["viewing"] == {"concern_code": "ZZC-1", "accepted": True, "rejected": None, "how": None}
    assert any(x["op"] == "focus" and x["why"] == "view" and x["to"] == "ZZC-1" for x in c["diff"])
    assert c["after"]["focus"] == "ZZC-1"
    row2, _ = _post(run, "그럼 다른 건요", "ZZC-404")
    assert row2["consult"]["viewing"]["accepted"] is False and row2["consult"]["viewing"]["rejected"] == "not_in_object"


def test_s6_t19_text_new_concern_wins(run):
    run.turn("가짜고민 시술")
    row, _ = _post(run, "가짜윗고민 은요", "ZZC-1")
    assert row["consult"]["viewing"]["rejected"] == "text" and row["consult"]["after"]["focus"] == "ZZC-2"


def test_s6_t19_multiple_codes_pick_latest_and_sub_concern_enters(run):
    run.turn("가짜고민 시술")
    run.turn("가짜윗고민 은요")
    row, _ = _post(run, "이건요", "ZZC-1,ZZC-2")
    assert row["consult"]["viewing"]["concern_code"] == "ZZC-2"  # 객체에서 가장 최근
    row2, _ = _post(run, "이건 어때요", "ZZC-3")  # W-1 — 하위 고민 점
    c = row2["consult"]
    it = next(x for x in c["after"]["concerns"] if x["code"] == "ZZC-3")
    assert it["source"]["how"] == "view" and c["after"]["focus"] == "ZZC-3"  # B-2
    assert ("add", "ZZC-3", "view") in {(x["op"], x["code"], x["why"]) for x in c["diff"]}
    assert c["viewing"]["how"] == "view"  # 검수 W-5 — 장부에 누르기로 든 고민이 보인다
    from clinic_chat.consult.render import state_view

    fields = dict(state_view(row2)["fields"])
    assert '"ZZC-3"' in fields["how=view"] and '"how": "view"' in fields["viewing"]  # 패널에 갈라 보인다
    assert "how=view" not in dict(state_view(row)["fields"])  # 말한 고민만인 턴은 칸 없음


def test_s6_t19_viewing_on_talk_turn(tmp_path, fake_cli):
    from tests.test_v03_turn_kind import jv

    r = Run(tmp_path, fake_cli, hub=state_hub(sites=SITES), noul=jv(), chat_ask_max=0)
    try:
        r.turn("가짜고민 시술")
        r.turn("가짜윗고민 은요")
        r.set_noul(jv(talk=0.9))
        row, body = _post(r, "음 그렇군요", "ZZC-1")
        c = row["consult"]
        assert c["turn_kind"]["kind"] == "talk" and c["viewing"]["accepted"] is True  # B-4
        assert c["after"]["focus"] == "ZZC-1" and "event: list" in body  # TT2 — focus 가 옮아 리스트가 간다
    finally:
        r.close()


def test_fd9_face_files_unchanged_and_t23():
    for f, sha in FACE_SHA.items():
        assert hashlib.sha1((ROOT / f).read_bytes()).hexdigest() == sha, f  # shasum(sha1) 값
    body = re.sub(r"(?s)\{#.*?#\}", "", (TEMPLATES / "partials" / "sites.html").read_text(encoding="utf-8"))
    assert not re.search(r"[가-힣]", body)
