"""SPEC-010 WP-3b — 다음 질문 축(§5-4 AX1 ~ AX7). 외부 호출 0(순수 함수 · 가짜 허브 · 고정본 `$J`)."""

from __future__ import annotations

import asyncio
import json

import pytest

from clinic_chat.consult import ask_axis as ax
from clinic_chat.consult import kos_map as km
from tests.conftest import KOS_FIXTURE, KOS_FIXTURE_ITEMS
from tests.fake_hub import FakeHub
from tests.state_fixture import Run, state_hub
from tests.v04_fixture import entry_rows, list_route


@pytest.fixture(autouse=True)
def _clear():
    ax.clear_cache()
    yield
    ax.clear_cache()


def kos() -> km.KosMap:
    return km.load(KOS_FIXTURE, expect_items=KOS_FIXTURE_ITEMS)


# ---------- 순수 함수 pick(AX1 · AX2 · AX4 · AX5) ----------


def L(n: int) -> list[str]:
    return [f"I{i:03d}" for i in range(n)]


def order(xs):
    return {c: i for i, c in enumerate(xs)}


def test_ax1_small_list_no_axis():
    xs = L(4)
    out = ax.pick(xs, {"site": {c: [c] for c in xs}}, order=order(xs))
    assert out["axis"] is None and out["excluded"]["*"] == "list_small"
    assert ax.pick(L(5), {"site": {c: [c] for c in L(5)}}, order=order(L(5)))["axis"] == "site"


def test_ax4_exclusions_and_score():
    xs = L(10)
    vals = {
        "site": {c: ["S1"] for c in xs},  # 하나뿐 → groups_lt2
        "concern": {
            c: (["C1"] if i < 2 else ["C2"] if i < 4 else []) for i, c in enumerate(xs)
        },  # 없음 6 > 5 → none_over_half
        "effect": {c: ["E1", "E2"] for c in xs},  # 가장 큰 묶음 = L 전체 → all(N14)
        "product": {c: (["P1"] if i < 6 else ["P2"]) for i, c in enumerate(xs)},  # 6 · 4 → 점수 6
    }
    out = ax.pick(xs, vals, order=order(xs))
    assert out["excluded"] == {"site": "groups_lt2", "concern": "none_over_half", "effect": "all"}
    assert out["axis"] == "product" and out["scores"]["product"] == 6 and out["scores"]["effect"] == 10
    assert [o["value"] for o in out["options"]] == ["P1", "P2"] and out["options"][0]["n"] == 6


def test_ax4_tie_order_site_first_and_filled_excluded():
    xs = L(6)
    half = {c: (["A"] if i < 3 else ["B"]) for i, c in enumerate(xs)}
    vals = {"site": dict(half), "concern": dict(half), "product": dict(half)}
    assert ax.pick(xs, vals, order=order(xs))["axis"] == "site"  # 동점 = 부위 → 고민 → 효과 → 제품
    assert ax.pick(xs, vals, order=order(xs), filled={"site"})["axis"] == "concern"  # 값 있는 칸은 축 아님(AX2)
    assert ax.pick(xs, vals, order=order(xs), skipped=["site", "concern"])["axis"] == "product"  # AX6 표지


def test_ax5_top5_and_tie_by_groups_order():
    xs = L(12)
    vals = {"product": {c: [f"P{i % 6}"] for i, c in enumerate(xs)}}  # 6 묶음 · 각 2
    out = ax.pick(xs, vals, order=order(xs), names={("product", "P0"): "이름0"})
    assert [o["value"] for o in out["options"]] == ["P0", "P1", "P2", "P3", "P4"]  # 동점 → 처음 나온 순 · 앞 5
    assert out["options"][0]["name"] == "이름0" and out["options"][1]["name"] == "P1"


# ---------- AX7 표본(WP-0 실측 고정 — research-v05-facts AX7) ----------


def test_ax7_sample1_palja_product_5():
    """팔자주름 L = 23(고민 칸 참) — 부위 23 · 효과 23 은 L 전체라 뺌 → 제품 5(소프웨이브 · PRD-402 · PRD-400 동점 5)."""
    xs = L(23)
    prod = ["PRD-435"] * 5 + ["PRD-402"] * 5 + ["PRD-400"] * 5 + ["PRD-150"] * 4 + ["PRD-416"] * 3 + ["PRD-X"] * 1
    vals = {
        "site": {c: (["SITE-007", "SITE-001"] if i < 7 else ["SITE-007"]) for i, c in enumerate(xs)},
        "effect": {c: ["EFF-020", "EFF-070"] for c in xs},
        "concern": {c: ["CON-009"] for c in xs},
        "product": {c: [prod[i]] for i, c in enumerate(xs)},
    }
    out = ax.pick(xs, vals, filled={"concern"}, order=order(xs))
    assert out["axis"] == "product" and out["scores"]["product"] == 5
    assert out["excluded"]["site"] == "all" and out["excluded"]["effect"] == "all"
    assert [o["value"] for o in out["options"]] == ["PRD-435", "PRD-402", "PRD-400", "PRD-150", "PRD-416"]


def test_ax7_sample2_botox_site_21_and_skipped():
    """보톡스 L = 106(시술 칸 참) — 첫 턴은 고민 · 효과 빠짐(AX6) → 부위 21(턱)."""
    xs = L(106)
    sizes = (
        [("SITE-072", 21), ("SITE-007", 16), ("SITE-057", 12), ("SITE-102", 8), ("SITE-035", 8)]
        + [(f"SITE-X{i}", 8) for i in range(5)]
        + [("SITE-Y", 1)]
    )
    site = [s for s, n in sizes for _ in range(n)]
    prod = [p for p, n in (("PRD-022", 33), ("PRD-003", 26), ("PRD-008", 24), ("PRD-001", 23)) for _ in range(n)]
    vals = {"site": {c: [site[i]] for i, c in enumerate(xs)}, "product": {c: [prod[i]] for i, c in enumerate(xs)}}
    out = ax.pick(xs, vals, order=order(xs), skipped=["concern", "effect"])
    assert out["axis"] == "site" and out["scores"]["site"] == 21 and out["scores"]["product"] == 33
    assert out["options"][0] == {"value": "SITE-072", "name": "SITE-072", "n": 21}
    assert out["skipped_axes"] == ["concern", "effect"]


def test_ax7_sample3_ldm_three_no_axis():
    xs = L(3)
    assert ax.pick(xs, {"site": {c: [c] for c in xs}}, order=order(xs))["axis"] is None


# ---------- 값 표(AX3 · AX6 — 가짜 허브 · 고정본) ----------


def test_level2_sites():
    m = kos()
    assert ax.level2(m, "ZS-073") == "ZS-072"  # 턱끝 → 턱(얼굴 바로 아래)
    assert ax.level2(m, "ZS-072") == "ZS-072" and ax.level2(m, "ZS-007") == "ZS-007"
    assert ax.level2(m, "ZS-079") == "ZS-079"  # 목 = 몸 바로 아래
    assert ax.level2(m, "ZS-147") == "ZS-147" and ax.level2(m, "ZS-001") == "ZS-001"


def facets(p):
    node, f = p.get("node"), p.get("facet")
    if f == "indication":
        code = {"procedure_case:ZCAS-001": "ZC-1", "procedure_case:ZCAS-002": "ZC-2"}.get(node)
        rows = (
            [{"concern_code": code, "concern_name": "가짜" + code, "reach": "케이스 직접", "layer": {"role": "자기"}}]
            if code
            else []
        )
        rows.append({"concern_code": "ZC-PROD", "reach": "제품 경유", "layer": {"role": "자기"}})
        return {"rows": rows}
    if f == "outcome":
        return {"rows": [{"effect_code": "ZE-1", "effect_name": "가짜효과"}, {"effect_code": "", "onset_text": "x"}]}
    return {"rows": []}


def scope(p):
    return {
        "holders": {
            "product:ZPRD-002": [
                {"holder_table": "product", "holder_code": "ZPRD-001", "holder_name": "가짜톡스", "role": "상위 제품"}
            ]
        }.get(p.get("node"), [])
    }


def hub():
    return FakeHub({"facets": facets, "scope": scope}).client(page_limit=50)


def test_axis_values_concern_effect_product():
    m = kos()
    vals, names, skipped, calls = asyncio.run(ax.axis_values(m, hub(), "g", ["ZZ-BTX-A", "ZZ-BTX-B"]))
    assert vals["concern"] == {"ZZ-BTX-A": ["ZC-1"], "ZZ-BTX-B": ["ZC-2"]}  # 자기 · 케이스 직접만
    assert vals["effect"]["ZZ-BTX-A"] == ["ZE-1"]  # N3 — 빈 경과 줄 뺌
    assert vals["product"] == {"ZZ-BTX-A": ["ZPRD-001"], "ZZ-BTX-B": ["ZPRD-001"]}  # N9 상위 제품으로 올림
    assert vals["site"] == {"ZZ-BTX-A": ["ZS-072"], "ZZ-BTX-B": ["ZS-057"]}
    assert names[("concern", "ZC-1")] == "가짜ZC-1" and names[("product", "ZPRD-001")] == "가짜톡스"
    assert skipped == [] and calls == 4 + 2  # 케이스 둘 × facet 둘 + scope 둘
    _, _, _, calls2 = asyncio.run(ax.axis_values(m, hub(), "g", ["ZZ-BTX-A", "ZZ-BTX-B"]))
    assert calls2 == 0  # 프로세스 캐시(gold_hash, 노드, facet)


def test_axis_values_call_limit_skips_concern_effect():
    vals, _, skipped, calls = asyncio.run(ax.axis_values(kos(), hub(), "g2", ["ZZ-BTX-A", "ZZ-BTX-B"], call_max=3))
    assert skipped == ["concern", "effect"] and "concern" not in vals and "effect" not in vals  # AX6
    assert "site" in vals and "product" in vals and calls == 3 + 2  # 한도까지 캐시 채움 + scope
    vals2, _, skipped2, _ = asyncio.run(ax.axis_values(kos(), hub(), "g2", ["ZZ-BTX-A", "ZZ-BTX-B"], call_max=3))
    assert skipped2 == [] and vals2["concern"]["ZZ-BTX-A"] == ["ZC-1"]  # 다음 턴 — 남은 호출 1 ≤ 한도


# ---------- 한 턴(0.2 ask 연결 · 장부) ----------

EXTRA = [
    {
        "entity_table": "procedure",
        "entity_code": "ZPRC-001",
        "entity_name": "가짜보톡스시술",
        "name_kind": "정식",
        "text": "가짜보톡스시술",
    }
]


def understand_out(targets) -> str:
    raw = json.dumps(
        {
            "targets": [{"table": t, "code": c, "sure": True} for t, c in targets],
            "kind": "new",
            "sites": [],
            "withdraw": [],
            "kinds": [],
            "out": False,
        },
        ensure_ascii=False,
    )
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": raw,
            "duration_api_ms": 900,
            "usage": {"input_tokens": 3, "output_tokens": 20},
            "modelUsage": {"claude-test": {}},
        },
        ensure_ascii=False,
    )


def test_turn_axis_ledger_and_close(tmp_path, fake_cli):
    h = state_hub()
    h.routes["resolve"] = list_route(entry_rows() + EXTRA)
    r = Run(tmp_path, fake_cli, hub=h, chat_ask_list_min=1)  # 고정본 목록이 작아 한도를 1 로
    try:
        (fake_cli[1] / "understand.json").write_text(understand_out([("procedure", "ZPRC-001")]), encoding="utf-8")
        row, _, _ = r.turn("가짜보톡스시술")
        a = row["consult"]["ask_axis"]
        assert a["axis"] == "site" and [o["name"] for o in a["options"]] == ["턱", "입"] and a["scores"]["site"] == 1
        assert row["consult"]["close"]["by"] == "ask" and row["consult"]["ask_why"] == "axis"
    finally:
        r.close()


def test_turn_no_axis_when_list_small(tmp_path, fake_cli):
    h = state_hub()
    h.routes["resolve"] = list_route(entry_rows() + EXTRA)
    r = Run(tmp_path, fake_cli, hub=h)  # 기본 한도 4 · 목록 2
    try:
        (fake_cli[1] / "understand.json").write_text(understand_out([("procedure", "ZPRC-001")]), encoding="utf-8")
        row, _, _ = r.turn("가짜보톡스시술")
        assert row["consult"]["ask_axis"]["axis"] is None and row["consult"]["ask_why"] != "axis"
    finally:
        r.close()
