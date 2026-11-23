"""SPEC-010 WP-5 — 서버 장바구니(§4-6 CT1 ~ CT8 · OP1 ~ OP8 · RT1) · 장부 `cart` · `cart_saved`(가격 0). 외부 호출 0(고정본 `$J`)."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from clinic_chat.config import ROOT
from clinic_chat.consult.fake_turn import make
from clinic_chat.consult.server import create_app
from tests.conftest import make_settings

REAL = ROOT / "data" / "kos" / "myeongdong-mapping-20261120-v3.json"
CID = "cartconv00000001"


def app(tmp_path, **kw):
    s = make_settings(tmp_path, chat_dir=str(tmp_path / "chat"), chat_fake_turn=True, **kw)
    return create_app(s, turn_fn=make(0))


def post(c, **f):
    return c.post(f"/c/{CID}/cart", data=f)


def rows(tmp_path, phase):
    f = tmp_path / "chat" / "turns.jsonl"
    return [r for r in map(json.loads, f.read_text(encoding="utf-8").splitlines()) if r["phase"] == phase]


def test_op1_add_is_item_only_and_ct6_ledger(tmp_path):
    c = TestClient(app(tmp_path))
    h = post(c, op="add", code="ZZ-BTX-A").text
    assert 'data-cart-code="ZZ-BTX-A"' in h and 'data-cart-count="1"' in h and 'cs-cart__opt"' not in h  # 옵션 0 줄
    assert "data-rec-no-pick" in h and ">가짜 턱 보톡스 A<" in h  # OP6 — 옵션 안 고름 이름
    assert rows(tmp_path, "cart")[-1]["cart"] == [{"code": "ZZ-BTX-A", "lines": []}]
    post(c, op="add", code="ZZ-BTX-A")
    assert rows(tmp_path, "cart")[-1]["cart"] == [{"code": "ZZ-BTX-A", "lines": []}]  # 같은 진료항목 한 번


def test_op3_op4_lines_multi_same_and_delete(tmp_path):
    c = TestClient(app(tmp_path))
    post(c, op="add", code="ZZ-BTX-A")
    post(c, op="line_add", code="ZZ-BTX-A", product_key="P1", option_key="P1-1")
    h = post(c, op="line_add", code="ZZ-BTX-A", product_key="P1", option_key="P1-1").text  # 같은 옵션 두 줄
    assert h.count('class="cs-cart__opt"') == 2 and 'aria-label="가짜 턱 보톡스 1회"' in h
    assert '<span class="cs-cart__opt-price">1000</span>' in h  # 원본 정가 숫자(PR1 · OP4)
    assert 'data-rec-card="1"' in h and "data-rec-no-pick" not in h
    h2 = post(c, op="line_del", code="ZZ-BTX-A", idx="0").text
    assert h2.count('class="cs-cart__opt"') == 1
    assert rows(tmp_path, "cart")[-1]["cart"] == [{"code": "ZZ-BTX-A", "lines": [["P1", "P1-1"]]}]


def test_op3_rejects_group_closed_and_other_items_option(tmp_path):
    c = TestClient(app(tmp_path))
    post(c, op="add", code="ZZ-BTX-A")
    post(c, op="add", code="ZZ-BTX-B")
    assert post(c, op="line_add", code="ZZ-BTX-A", product_key="P3", option_key="P3-1").status_code == 422  # group(OP8)
    assert post(c, op="line_add", code="ZZ-BTX-B", product_key="P6", option_key="P6-1").status_code == 422  # 판매 종료
    assert (
        post(c, op="line_add", code="ZZ-BTX-A", product_key="P1", option_key="P1-3").status_code == 422
    )  # 다른 진료항목
    assert post(c, op="line_add", code="ZZ-LIFT", product_key="P2", option_key="P2-1").status_code == 422  # 장바구니 밖
    assert post(c, op="line_del", code="ZZ-BTX-A", idx="3").status_code == 422
    assert (
        post(c, op="add", code="ZZ-NONE").status_code == 404 and post(c, op="jump", code="ZZ-BTX-A").status_code == 422
    )


def test_op5_no_button_for_single0_and_rc6_no_price(tmp_path):
    c = TestClient(app(tmp_path))
    h = post(c, op="add", code="ZZ--").text  # group 만
    li = h.split('data-cart-code="ZZ--"')[1]
    assert "data-opt-open" not in li.split("</li>")[0] and "data-rec-no-price" in h and ">가짜 목 제모<" in h


def test_op7_delete_item_and_clear(tmp_path):
    c = TestClient(app(tmp_path))
    post(c, op="add", code="ZZ-BTX-A")
    post(c, op="line_add", code="ZZ-BTX-A", product_key="P1", option_key="P1-1")
    post(c, op="add", code="ZZ-LIFT")
    h = post(c, op="del", code="ZZ-BTX-A").text
    assert 'data-cart-code="ZZ-BTX-A"' not in h and rows(tmp_path, "cart")[-1]["cart"] == [
        {"code": "ZZ-LIFT", "lines": []}
    ]
    h2 = post(c, op="clear").text
    assert "data-cart-empty" in h2 and 'data-cart-count="0"' in h2 and "cs-sk--price" in h2  # 빈 장바구니 = 스켈레톤


def test_group_card_and_values(tmp_path):
    c = TestClient(app(tmp_path))
    for code, pk, ok in (("ZZ-BTX-A", "P1", "P1-1"), ("ZZ-LIFT", "P2", "P2-1")):
        post(c, op="add", code=code)
        h = post(c, op="line_add", code=code, product_key=pk, option_key=ok).text
    cards = re.findall(r'data-rec-card="(\d)"', h)
    assert cards == ["1", "2"]
    first = h.split('data-rec-card="1"')[1].split('data-rec-card="2"')[0]
    assert ">보톡스+리프팅<" in first and ">3500<" in first and ">6000<" in first  # group 이벤트가 3500 · 정가 6000
    assert '<span class="cs-rec__cover">가짜 턱 보톡스 A</span>' in first and 'data-slot="rec-tag-event"' in first
    second = h.split('data-rec-card="2"')[1]
    assert ">1000<" in second and ">3000<" in second  # P1-1 정가 1000 + P2-1 이벤트가 3000 = 4000


def test_restart_restores_cart_and_page(tmp_path):
    c = TestClient(app(tmp_path))
    post(c, op="add", code="ZZ-BTX-A")
    post(c, op="line_add", code="ZZ-BTX-A", product_key="P1", option_key="P1-2")
    page = TestClient(app(tmp_path)).get(f"/c/{CID}").text  # 재시작
    assert 'data-cart-code="ZZ-BTX-A"' in page and ">가짜 턱 보톡스 5회<" in page and 'data-cart-count="1"' in page
    assert 'class="cs-top__n" data-cart-n2>0<' in page  # 수는 consult.js 가 cart 조각 값으로 맞춤


def test_ct7_save_and_ledger_has_no_prices(tmp_path):
    c = TestClient(app(tmp_path))
    assert c.post(f"/c/{CID}/cart/save").status_code == 422  # 빈 장바구니
    post(c, op="add", code="ZZ-LIFT")
    post(c, op="line_add", code="ZZ-LIFT", product_key="P2", option_key="P2-1")
    assert c.post(f"/c/{CID}/cart/save").status_code == 204
    saved = rows(tmp_path, "cart_saved")[-1]
    assert saved["codes"] == ["ZZ-LIFT"] and saved["best_keys"] == [["P2", "P2-1"]]
    raw = (tmp_path / "chat" / "turns.jsonl").read_text(encoding="utf-8")
    assert not re.search(r"\b(5000|3000|6000|3500)\b", raw) and not re.search(
        r'"(price_amount|event_price|total|list_total|used)"', raw
    )  # JS6 — 가격 숫자 0
    assert rows(tmp_path, "cart")[-1]["rec"]["cards"] == [[["P2", "P2-1"]]]


@pytest.mark.skipif(not REAL.is_file(), reason="실물 $J(git 무시) 없음")
def test_rt1_codes_real(tmp_path):
    c = TestClient(app(tmp_path, chat_kos_map=str(REAL), chat_kos_map_items=324))
    for code in ("WHITENING IV DRIP + VITAMIN IV DRIP", "26-시트", "26-", "26--", "26---", "26----"):
        assert post(c, op="add", code=code).status_code == 200, code
    assert len(rows(tmp_path, "cart")[-1]["cart"]) == 6
