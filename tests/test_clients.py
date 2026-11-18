"""WP-1 — 허브 REST 클라이언트 · Jev 클라이언트(모의만)."""

from __future__ import annotations

import pytest

from clinic_chat.clients.hub_rest import HubError, collect_edge_gold_ids, collect_gids, encode_params
from clinic_chat.clients.jev_client import JevError, choice, choice_stats, noul
from tests.conftest import make_settings
from tests.fake_hub import FakeHub, row
from tests.fake_jev import FakeJev

ROWS6 = [row("g_profile", f"GPF-9000{i}", subject_table="procedure", subject_code="ZZP-1") for i in range(6)]


async def test_read_all_three_pages_to_end():
    hub = FakeHub({"facets": {"rows": ROWS6, "ledger_check": []}}, limit_cap=2)
    ra = await hub.client().read_all("facets", {"node": "procedure:ZZP-1", "facet": "profile"})
    assert (ra.pages, ra.read, ra.total, ra.complete) == (3, 6, 6, True)
    assert ra.cursors == [None, "2", "4"]
    assert collect_gids(ra.as_response()) == [r["gid"] for r in ROWS6]


async def test_read_all_mid_error_is_incomplete():
    hub = FakeHub({"facets": {"rows": ROWS6}}, limit_cap=2, fail_on={"facets"})
    ra = await hub.client().read_all("facets", {"node": "procedure:ZZP-1", "facet": "profile"})
    assert ra.complete is False and ra.error and ra.read == 2


async def test_errors_by_http_status():
    hub = FakeHub({"scope": (404, {"detail": "not_found: x"}), "facets": (422, {"detail": "invalid_argument: y"}),
                   "pair": (400, {"detail": "cursor_invalid: z"})})
    c = hub.client()
    for tool, kind in (("scope", "not_found"), ("facets", "invalid_argument"), ("pair", "cursor_invalid")):
        with pytest.raises(HubError) as e:
            await c.call(tool, {})
        assert e.value.kind == kind


async def test_outside_lists_only_first_page():
    g = [{"candidate_table": "procedure", "candidate_code": f"ZZP-{i}", "rows": [row("g_indication", f"GIN-9000{i}")]}
         for i in range(3)]
    hub = FakeHub({"concern_candidates": {"groups": g, "modality_only": [row("g_indication", "GIN-90099")],
                                          "unreached": [], "ledger_check": []}})
    ra = await hub.client().read_all("concern_candidates", {"concern_code": "ZZC-1"})
    assert ra.complete and ra.read == 3
    assert "GIN-90099" in collect_gids(ra.as_response())


def test_encode_params_lists_and_filters():
    q = encode_params({"node": "procedure:ZZP-1", "roles": ["자기", "물려받음"], "filters": {"adverse_code": "ZZA-1"},
                       "same_day_only": False, "b": None})
    assert ("roles", "자기,물려받음") in q and ("filters", "adverse_code=ZZA-1") in q and ("same_day_only", "false") in q
    assert all(k != "b" for k, _ in q)


def test_edge_gold_id_not_a_gid():
    resp = {"items": [{"edge_id": "GED-1", "gold_table": "g_pair", "gold_id": "GPR-90001", "node": {"node_id": "x"}}]}
    assert collect_gids(resp) == []
    assert collect_edge_gold_ids(resp) == ["GPR-90001"]


def test_choice_stats():
    assert choice_stats({"a": 0.6, "b": 0.3, "c": 0.1}) == (0.6, 2.0)


async def test_jev_answer_and_model(tmp_path):
    j = FakeJev(model="jev-x").jev(make_settings(tmp_path))
    a = await j.ask({"q": 1}, {"k": choice("pick", {"x": "X", "y": "Y"}), "n": noul("yes?")})
    assert a.model == "jev-x" and a.choices["k"]["choice"] == "x" and a.nouls["n"] == 0.9


@pytest.mark.parametrize("kw", [{"status": 500}, {"timeout": True}])
async def test_jev_errors_raise(tmp_path, kw):
    j = FakeJev(**kw).jev(make_settings(tmp_path))
    with pytest.raises(JevError):
        await j.ask({"q": 1}, {"n": noul("yes?")})


async def test_jev_no_key_raises(tmp_path):
    from clinic_chat.clients.jev_client import Jev, make_client

    s = make_settings(tmp_path, typesafe_api_key=None)
    with pytest.raises(JevError):
        await Jev(make_client(s)).ask({}, {"n": noul("x")})


# ───── WP-1 — coverage(도구 9) · 새 resolve 꼴 · 모의 ↔ 실물 키 집합(W-5) ─────

# 18106 실물 `hub_rest --ping` 이 낸 키 집합(11-16 · gold_hash 8accbb21) — 모의가 같은 꼴인지 본다
REAL_KEYS = {"matched": ["covered_by", "span", "start"], "coverage.items[]": ["kinds", "name", "node"],
             "coverage.items[].kinds[]": ["inherited", "kind", "lower", "lower_holders", "self"],
             "coverage": ["build", "items", "next_cursor", "request", "total"]}


async def test_coverage_splits_over_64_and_keeps_order():
    from tests.walk_fixture import make_hub

    hub = make_hub(coverage={f"procedure:ZZP-{i}": {"overview": (1, 0, 0)} for i in range(70)})
    h = hub.client()
    nodes = [f"procedure:ZZP-{i}" for i in range(70)]
    r = await h.coverage(nodes)
    assert [x["node"] for x in r["items"]] == nodes and r["calls"] == 2
    assert len([q for t, q in hub.requests if t == "coverage"]) == 2


async def test_fake_shapes_equal_real_ping_keys():
    from tests.walk_fixture import P1, cand, make_hub

    hub = make_hub(resolve=[cand(P1)], coverage={"procedure:ZZP-1": {"overview": (1, 0, 0)}})
    h = hub.client()
    cov = await h.coverage(["procedure:ZZP-1"])
    it = cov["items"][0]
    assert sorted(cov.keys() - {"calls"}) == REAL_KEYS["coverage"]
    assert sorted(it) == REAL_KEYS["coverage.items[]"] and sorted(it["kinds"][0]) == REAL_KEYS["coverage.items[].kinds[]"]
    m = (await h.resolve("가짜시술"))["candidates"][0]["matched"]
    assert sorted(m) == REAL_KEYS["matched"]


def test_no_particle_word_in_app():
    from pathlib import Path

    from clinic_chat.config import ROOT
    for p in (Path(ROOT) / "clinic_chat").rglob("*.py"):
        assert "particle" not in p.read_text(encoding="utf-8"), p.name
