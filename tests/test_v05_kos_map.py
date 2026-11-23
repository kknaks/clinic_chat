"""SPEC-010 WP-1 — `kos_map.py`(JS1 ~ JS6) · 설정 · 장부 `kos_map` 칸. 외부 호출 0(실물 v3 는 있으면만 읽음)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clinic_chat.config import ROOT
from clinic_chat.consult import kos_map as km
from clinic_chat.consult.server import create_app
from tests.conftest import KOS_FIXTURE, KOS_FIXTURE_ITEMS, make_settings

REAL = ROOT / "data" / "kos" / "myeongdong-mapping-20261120-v3.json"
# sha 는 고정하지 않는다 — WP-H 뒤 meta 해시만 바뀐다(WH5 · items · products 바이트 같음)
need_real = pytest.mark.skipif(not REAL.is_file(), reason="실물 $J(git 무시) 없음")


def fx() -> dict:
    return json.loads(KOS_FIXTURE.read_text(encoding="utf-8"))


def write(tmp_path: Path, d: dict, name: str = "kos.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def load_fx(d: dict, tmp_path: Path) -> km.KosMap:
    return km.load(write(tmp_path, d), expect_items=KOS_FIXTURE_ITEMS)


# ---------- JS5 색인 여덟(고정본) ----------


def test_indexes_on_fixture():
    m = km.load(KOS_FIXTURE, expect_items=KOS_FIXTURE_ITEMS)
    assert set(m.item) == {"ZZ-BTX-A", "ZZ-BTX-B", "ZZ-LIFT", "ZZ IV DRIP + VIT", "ZZ-시트", "ZZ--"}  # 1 · RT1 꼴 코드
    assert m.by_case["ZCAS-010"] == m.by_case["ZCAS-011"] == ("ZZ-LIFT",)  # 2 — 케이스 여럿
    assert m.by_proc["ZPRC-001"] == ("ZZ-BTX-A", "ZZ-BTX-B")  # 3 — groups 순서
    assert m.by_product["ZPRD-010"] == ("ZZ-LIFT",)  # 4
    assert m.by_site["ZS-007"] == ("ZZ-LIFT", "ZZ-시트") and m.by_site["ZS-001"] == ("ZZ-LIFT",)  # 5
    assert list(m.by_kos_procedure) == ["가짜 보톡스", "가짜 리프팅", "가짜 수액", "가짜 시트팩", "가짜 제모"]  # 6
    assert m.site_children["ZS-007"] == ("ZS-072", "ZS-057") and m.site_children["ZS-147"] == ("ZS-079",)  # 7
    assert m.covers_of["ZZ-BTX-A"] == (("P1", "P1-1"), ("P1", "P1-2"), ("P3", "P3-1"), ("P5", "P5-1"))  # 8
    assert m.covers_of["ZZ IV DRIP + VIT"] == ()
    assert m.option[("P3", "P3-1")]["type"] == "group" and m.option_product[("P6", "P6-1")]["closed"] is True
    assert "ZZ-BTX-A" not in m.by_case.get("", ()) and "ZZ IV DRIP + VIT" not in {
        c for v in m.by_case.values() for c in v
    }


def test_site_walks_and_order():
    m = km.load(KOS_FIXTURE, expect_items=KOS_FIXTURE_ITEMS)
    assert m.descendants("ZS-007") == ["ZS-007", "ZS-072", "ZS-057", "ZS-073"]
    assert m.descendants("ZS-NONE") == []
    assert m.ancestors("ZS-079") == ["ZS-147", "ZS-001"] and m.ancestors("ZS-007") == []
    assert m.sort_codes(["ZZ--", "ZZ-BTX-B", "ZZ-BTX-A", "ZZ--"]) == ["ZZ-BTX-A", "ZZ-BTX-B", "ZZ--"]


def test_stamp_has_no_prices():
    m = km.load(KOS_FIXTURE, expect_items=KOS_FIXTURE_ITEMS)
    st = m.stamp()
    assert set(st) == {"sha", "made_at", "hub_gold_hash"} and st["sha"] == m.sha256 and len(st["sha"]) == 64


# ---------- JS2 꼴 검사 — 하나씩 어긋나면 시작 실패 ----------


def _first_opt(d):
    return d["products"][0]["options"][0]


BREAKS = {
    "version": lambda d: d["meta"].update(version="v2"),
    "no_products": lambda d: d.pop("products"),
    "no_categories": lambda d: d.pop("product_categories"),
    "no_sites": lambda d: d.pop("sites"),
    "item_count": lambda d: d["items"].pop(),
    "dup_code": lambda d: d["items"][1].update(kos_item_code="ZZ-BTX-A"),
    "ref_missing": lambda d: d["items"][0]["option_refs"].append({"product_key": "P1", "option_key": "P1-99"}),
    "cover_missing": lambda d: _first_opt(d)["covers"].append("ZZ-NOPE"),
    "refs_ne_covers": lambda d: _first_opt(d)["covers"].append("ZZ-LIFT"),
    "dup_product_key": lambda d: d["products"][1].update(product_key="P1"),
    "dup_option_key": lambda d: d["products"][0]["options"][1].update(option_key="P1-1"),
    "option_type": lambda d: _first_opt(d).update(type="consult"),
    "site_code_missing": lambda d: d["items"][0]["hub"]["site_codes"].append("ZS-404"),
    "face_shape": lambda d: d["sites"]["ZS-072"].update(shape="area"),
    "face_no_points": lambda d: d["sites"]["ZS-057"].update(points=[]),
    "site_parent": lambda d: d["sites"]["ZS-079"].update(parent_code="ZS-404"),
    "groups_miss": lambda d: d["groups"].pop(),
    "groups_twice": lambda d: d["groups"][1]["items"].append("ZZ-BTX-A"),
}


@pytest.mark.parametrize("name", sorted(BREAKS))
def test_shape_breaks_fail(tmp_path, name):
    d = copy.deepcopy(fx())
    BREAKS[name](d)
    with pytest.raises(km.KosMapError) as e:
        load_fx(d, tmp_path)
    assert "kos_map:" in str(e.value)


def test_load_empty_missing_notjson(tmp_path):
    for bad in (None, "", tmp_path / "none.json"):
        with pytest.raises(km.KosMapError):
            km.load(bad)
    p = tmp_path / "x.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(km.KosMapError):
        km.load(p, expect_items=None)


def test_error_text_has_no_price(tmp_path):
    d = copy.deepcopy(fx())
    _first_opt(d)["covers"].append("ZZ-NOPE")
    with pytest.raises(km.KosMapError) as e:
        load_fx(d, tmp_path)
    assert "1000" not in str(e.value) and "ZZ-NOPE" in str(e.value)  # JS6 — 키 · 코드만


# ---------- JS3 sha ----------


def test_sha_setting(tmp_path):
    sha = km.file_sha(KOS_FIXTURE.read_bytes())
    for ok in (sha, sha[:8], sha.upper()[:12], None, ""):
        assert km.load(KOS_FIXTURE, expect_sha=ok, expect_items=KOS_FIXTURE_ITEMS).sha256 == sha
    for bad in ("0" * 64, sha[:7], "deadbeef"):
        with pytest.raises(km.KosMapError):
            km.load(KOS_FIXTURE, expect_sha=bad, expect_items=KOS_FIXTURE_ITEMS)


# ---------- JS4 해시 대조 ----------


def test_hash_check():
    m = km.load(KOS_FIXTURE, expect_items=KOS_FIXTURE_ITEMS)
    assert km.hash_check(m, None)["hash_mismatch"] is None
    same = km.hash_check(m, lambda: {"build": {"gold_hash": m.hub_gold_hash}})
    assert same["hash_mismatch"] is False and same["hub_build_gold_hash"] == m.hub_gold_hash
    assert km.hash_check(m, lambda: {"build": {"gold_hash": "other"}})["hash_mismatch"] is True

    def boom():
        raise OSError("down")

    down = km.hash_check(m, boom)
    assert down["hash_mismatch"] is None and down["hub_error"] == "OSError"
    assert km.hash_check(m, lambda: {})["hub_error"] == "no_gold_hash"


# ---------- 서버 시작 · 장부 칸 ----------


def test_server_start_fails_without_or_bad_map(tmp_path):
    with pytest.raises(km.KosMapError):
        create_app(make_settings(tmp_path, chat_kos_map=None))
    d = copy.deepcopy(fx())
    d["meta"]["version"] = "v2"
    with pytest.raises(km.KosMapError):
        create_app(make_settings(tmp_path, chat_kos_map=str(write(tmp_path, d))))
    with pytest.raises(km.KosMapError):
        create_app(make_settings(tmp_path, chat_kos_map_sha="0" * 64))


def test_server_start_ledger_kos_map(tmp_path):
    s = make_settings(tmp_path, chat_dir=str(tmp_path / "chat"), chat_fake_turn=True)
    app = create_app(s, hub_health=lambda: {"build": {"gold_hash": "different"}})
    m = app.state.kos
    assert app.state.ctx.kos is m and len(m.item) == KOS_FIXTURE_ITEMS
    assert app.state.kos_check["hash_mismatch"] is True  # JS4 — 시작은 함
    with TestClient(app) as c:
        r = c.post("/c/kosconv0001/turns", data={"text": "안녕"}, headers={"HX-Request": "true"})
        assert r.status_code == 200
    start = [r for r in app.state.ctx.ledger.rows("kosconv0001") if r["phase"] == "start"][0]
    assert start["kos_map"] == {
        "sha": m.sha256,
        "made_at": m.meta["made_at"],
        "hub_gold_hash": m.hub_gold_hash,
        "hash_mismatch": True,
    }
    raw = (tmp_path / "chat" / "turns.jsonl").read_text(encoding="utf-8")
    assert '"price_amount"' not in raw and "event_price" not in raw  # JS6


# ---------- 실물 v3 ----------


@need_real
def test_real_v3_loads():
    sha = km.file_sha(REAL.read_bytes())
    m = km.load(REAL, expect_sha=sha)
    assert m.sha256 == sha and len(m.item) == 324 and len(m.groups) == 131 and len(m.sites) == 92
    assert len(m.by_case) == 137 and len(m.by_proc) == 43 and len(m.option) == 493
    assert sum(1 for c in m.covers_of.values() if not c) == 24
    for code in ("WHITENING IV DRIP + VITAMIN IV DRIP", "26-시트", "26-", "26--", "26---", "26----"):  # RT1
        assert code in m.item
    assert m.ancestors("SITE-079") == ["SITE-147", "SITE-001"]


@need_real
def test_real_v3_server_start(tmp_path):
    s = make_settings(
        tmp_path,
        chat_dir=str(tmp_path / "chat"),
        chat_kos_map=str(REAL),
        chat_kos_map_items=324,
        chat_kos_map_sha=km.file_sha(REAL.read_bytes())[:8],
    )
    app = create_app(s, hub_health=lambda: {"build": {"gold_hash": km.load(REAL).hub_gold_hash}})
    assert app.state.kos_check["hash_mismatch"] is False


@need_real
def test_real_v3_broken_copy_fails(tmp_path):
    d = json.loads(REAL.read_text(encoding="utf-8"))
    d["products"][0]["options"][0]["covers"].append("26-없는코드")
    p = write(tmp_path, d, "broken.json")
    with pytest.raises(km.KosMapError):
        create_app(make_settings(tmp_path, chat_kos_map=str(p), chat_kos_map_items=324))
