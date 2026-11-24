"""SPEC-010 WP-3 — 조건 누적 · 주제 → 진료항목(§5-0 · §5-0b · §5-1 ~ §5-3). 외부 호출 0(가짜 허브 · 가짜 CLI · 고정본 `$J`).

- 이름 일치 NM1 ~ NM7(고정본 + 실물 `$J` 가 있으면 E7 · E7b · E7d)
- 칸 → 진료항목(Detector): reach 「케이스 직접」 만 · 쪽 넘김 · 효과 길(케이스 · 시술 행만) · 계열 · 제품 하위 · 부위 SF1 ~ SF3 ·
  CZ1 · 국가 CD6 · 교집합 · 순서 · 같은 호출 한 번 · 허브 오류
- 주제 · 조건(topics.decide — 진료항목 셈은 장난감 집합): CD2 · CD4 · LS1 ~ LS3 · T1 ~ T19 · C1 ~ C7
- 한 턴 끝까지(가짜 CLI · 가짜 허브): `items` 이벤트 · 장부 `items` · `consult.topics` · 재시작 복원 · 머리 = 조건 이름
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from clinic_chat.config import ROOT
from clinic_chat.consult import detect as dt
from clinic_chat.consult import kos_map as km
from clinic_chat.consult import topics as tp
from clinic_chat.consult.server import create_app
from tests.conftest import KOS_FIXTURE, KOS_FIXTURE_ITEMS
from tests.fake_hub import FakeHub
from tests.state_fixture import Run, state_hub
from tests.v04_fixture import entry_rows, list_route

REAL = ROOT / "data" / "kos" / "myeongdong-mapping-20261120-v3.json"
need_real = pytest.mark.skipif(not REAL.is_file(), reason="실물 $J(git 무시) 없음")


def kos() -> km.KosMap:
    return km.load(KOS_FIXTURE, expect_items=KOS_FIXTURE_ITEMS)


def run(coro):
    return asyncio.run(coro)


# ---------- 이름 일치(NM) ----------


def test_nm_fixture_item_kos_and_spaces():
    m = kos()
    hits = dt.name_hits(m, "가짜턱 보톡스 A 받고 싶어요")  # NM2 — 공백 지움 · 「가짜 턱 보톡스 A」
    assert [(h.kind, h.items) for h in hits] == [("item", ["ZZ-BTX-A"])]
    hits = dt.name_hits(m, "가짜 보톡스 궁금")  # KOS 시술 이름 → 묶음 전부(NM4)
    assert [(h.kind, h.items) for h in hits] == [("kos_procedure", ["ZZ-BTX-A", "ZZ-BTX-B"])]
    assert dt.name_hits(m, "") == [] and dt.name_hits(m, "전혀 없는 말") == []


def test_nm3_longer_wins():
    m = kos()
    hits = dt.name_hits(m, "가짜 입꼬리 보톡스 B")  # 「가짜 입꼬리 보톡스 B」 ⊃ 짧은 이름 없음 · 진료항목 하나
    assert [h.name for h in hits] == ["가짜 입꼬리 보톡스 B"]


@need_real
def test_nm_real_e7_e7b_e7c_e7d():
    m = km.load(REAL)
    e7 = dt.name_hits(m, "자갈턱 주름보톡스 앨러간 하고 싶어요")
    assert any(h.kind == "item" and h.items == ["26-JUREJ-ALLG"] for h in e7)
    sof = dt.name_hits(m, "소프웨이브 받고 싶어요")  # E7b — 진료항목 이름 5 먼저 · KOS 시술 이름 묶음
    assert sof[0].kind == "item" and len(sof[0].items) == 5 and sof[1].kind == "kos_procedure"
    c = dt.name_hits(m, "입꼬리 보톡스 제오민")  # E7c
    assert any(
        "입꼬리보톡스 제오민" == h.name.replace(" ", "") or "입꼬리보톡스제오민" in h.name.replace(" ", "") for h in c
    )
    assert any(h.name == "LDM" for h in dt.name_hits(m, "LDM 받고 싶어요"))  # E7d — 낱말 경계
    assert not any(h.name == "LDM" for h in dt.name_hits(m, "LDMX 받고 싶어요"))
    por = [h for h in dt.name_hits(m, "포트라 받고 싶어요") if h.name == "포트라"]
    assert por and len({x for h in por for x in h.items}) == 5
    assert any(h.name == "셀코어" for h in dt.name_hits(m, "셀코어 궁금해요"))  # E8b — 케이스 0 이지만 이름 일치


# ---------- 칸 → 진료항목(가짜 허브) ----------


def cc_route(p):
    code = p.get("concern_code")
    rows = {
        "ZZC-A": [  # 「케이스 직접」 ZCAS-001 · 제품 경유 ZCAS-010 · 물려받은 행 ZCAS-002(E3)
            {
                "candidate_table": "procedure_case",
                "candidate_code": "ZCAS-001",
                "rows": [
                    {
                        "reach": "케이스 직접",
                        "candidate_table": "procedure_case",
                        "candidate_code": "ZCAS-001",
                        "concern_code": "ZZC-A",
                    }
                ],
            },
            {
                "candidate_table": "procedure_case",
                "candidate_code": "ZCAS-010",
                "rows": [
                    {
                        "reach": "제품 경유",
                        "candidate_table": "procedure_case",
                        "candidate_code": "ZCAS-010",
                        "concern_code": "ZZC-A",
                    }
                ],
            },
            {
                "candidate_table": "procedure_case",
                "candidate_code": "ZCAS-002",
                "rows": [
                    {
                        "reach": "케이스 직접",
                        "candidate_table": "procedure_case",
                        "candidate_code": "ZCAS-002",
                        "concern_code": "ZZC-UP",
                    }
                ],
            },
        ],
        "ZZC-E2": [
            {
                "candidate_table": "procedure_case",
                "candidate_code": "ZCAS-404",
                "rows": [
                    {
                        "reach": "케이스 직접",
                        "candidate_table": "procedure_case",
                        "candidate_code": "ZCAS-404",
                        "concern_code": "ZZC-E2",
                    }
                ],
            }
        ],
        "ZZC-PROD": [
            {
                "candidate_table": "procedure_case",
                "candidate_code": "ZCAS-001",
                "rows": [
                    {
                        "reach": "제품 경유",
                        "candidate_table": "procedure_case",
                        "candidate_code": "ZCAS-001",
                        "concern_code": "ZZC-PROD",
                    }
                ],
            }
        ],
        "ZZC-NECK": [
            {
                "candidate_table": "procedure_case",
                "candidate_code": "ZCAS-020",
                "rows": [
                    {
                        "reach": "케이스 직접",
                        "candidate_table": "procedure_case",
                        "candidate_code": "ZCAS-020",
                        "concern_code": "ZZC-NECK",
                    }
                ],
            }
        ],
    }.get(code, [])
    return {"groups": rows, "unreached": [{"reach": "제품 — 케이스 없음"}], "modality_only": []}


def scope_route(p):
    return {
        "holders": {
            "modality:ZM-1": [
                {"holder_table": "modality", "holder_code": "ZM-1", "role": "자기"},
                {"holder_table": "procedure", "holder_code": "ZPRC-020", "role": "하위 시술"},
                {"holder_table": "procedure_case", "holder_code": "ZCAS-001", "role": "하위 케이스"},
            ],
            "product:ZPRD-001": [
                {"holder_table": "product", "holder_code": "ZPRD-001", "role": "자기"},
                {"holder_table": "product", "holder_code": "ZPRD-002", "role": "하위 제품"},
                {"holder_table": "procedure", "holder_code": "ZPRC-010", "role": "물려받음"},
            ],
        }.get(p.get("node"), [])
    }


def facets_route(p):
    node, f = p.get("node"), p.get("facet")
    if f == "outcome" and node == "effect:ZEFF-1":
        return {
            "rows": [
                {"target_table": "procedure_case", "target_code": "ZCAS-002"},
                {"target_table": "procedure", "target_code": "ZPRC-010"},
                {"target_table": "modality", "target_code": "ZM-1"},
                {"target_table": "product", "target_code": "ZPRD-001"},
                {"target_table": "package", "target_code": "ZPKG-1"},
            ]
        }
    if f == "profile" and node.startswith("product:"):
        code = node.split(":", 1)[1]
        country = {"ZPRD-001": "KR", "ZPRD-002": "US", "ZPRD-010": ""}.get(code, "")
        return {"rows": [{"subject_table": "product", "subject_code": code, "country": country}]}
    return {"rows": []}


def fake_hub(**kw) -> FakeHub:
    return FakeHub({"concern_candidates": cc_route, "scope": scope_route, "facets": facets_route}, **kw)


def det(hub: FakeHub | None = None) -> tuple[dt.Detector, FakeHub]:
    h = hub or fake_hub()
    return dt.Detector(kos(), h.client(page_limit=2)), h


def v(t, c, name=""):
    return {"table": t, "code": c, "name": name}


def test_concern_case_direct_only_paging_and_dropped():
    d, h = det()
    r = run(d.items({"concern": [v("concern", "ZZC-A")]}))
    assert r.codes == ["ZZ-BTX-A", "ZZ-BTX-B"]  # 「케이스 직접」 만 · 제품 경유 ZCAS-010(LIFT) 0(E1 · E3b)
    assert r.dropped_reach == {"제품 경유": 1} and r.upper == ["ZZC-UP"]  # E3 — 물려받은 행
    assert sum(1 for t, p in h.requests if t == "concern_candidates") == 2  # 3 묶음 · 쪽 2 개(read_all 끝까지)
    assert all(
        p.get("upper") == "auto" and p.get("to_procedure") == "none" for t, p in h.requests if t == "concern_candidates"
    )


def test_concern_e2_e3b():
    d, _ = det()
    assert run(d.items({"concern": [v("concern", "ZZC-E2")]})).codes == []  # E2 — 케이스 직접인데 by_case 0
    r = run(d.items({"concern": [v("concern", "ZZC-PROD")]}))
    assert r.codes == [] and r.dropped_reach == {"제품 경유": 1}  # E3b


def test_effect_case_and_procedure_rows_only():
    d, _ = det()
    r = run(d.items({"effect": [v("effect", "ZEFF-1")]}))
    assert r.codes == ["ZZ-BTX-B", "ZZ-LIFT"]  # E15 · N2 — 케이스 ZCAS-002 + 시술 ZPRC-010
    assert r.dropped_reach == {"modality": 1, "product": 1, "package": 1}


def test_modality_product_procedure_case_paths():
    d, _ = det()
    assert run(d.items({"procedure": [v("modality", "ZM-1")]})).codes == ["ZZ--"]  # E6 — 계열 → 시술 행만
    assert run(d.items({"product": [v("product", "ZPRD-001")]})).codes == ["ZZ-BTX-A", "ZZ-BTX-B"]  # E8 하위 제품
    assert run(d.items({"procedure": [v("procedure", "ZPRC-001")]})).codes == ["ZZ-BTX-A", "ZZ-BTX-B"]  # E5
    assert run(d.items({"procedure": [v("procedure_case", "ZCAS-010")]})).codes == ["ZZ-LIFT"]  # E18


def test_site_sf2_sf3_and_cz1():
    d, _ = det()
    assert run(d.items({"site": [v("site", "ZS-072")]})).codes == [
        "ZZ-BTX-A",
        "ZZ-LIFT",
    ]  # E4 — 자손 + 조상 얼굴(얼굴 · 몸 가능)
    face = run(d.items({"site": [v("site", "ZS-007")]})).codes
    assert face == ["ZZ-BTX-A", "ZZ-BTX-B", "ZZ-LIFT"] and "ZZ-시트" not in face  # E4b — CZ1(케이스 0)
    assert run(d.items({"site": [v("site", "ZS-079")]})).codes == [
        "ZZ-LIFT",
        "ZZ--",
    ]  # 몸 부위 — 조상 몸(얼굴 · 몸 가능)
    assert run(d.items({"site": [v("site", "ZS-404")]})).codes == []


def test_name_values_bypass_cz1_and_order_first():
    d, _ = det()
    hits = dt.name_hits(kos(), "가짜 시트팩")
    turn, _ = tp.turn_conditions([], hits, None)
    assert run(d.items(turn)).codes == ["ZZ-시트"]  # E7 — 케이스 0 은 이름 일치로만(U-9)
    turn2, _ = tp.turn_conditions([{"table": "site", "code": "ZS-007", "name": "얼굴"}], hits, None)
    assert run(d.items(turn2)).codes == []  # 이름(시트) ∩ 부위(얼굴 — CZ1 로 시트 빠짐) = 0
    hits3 = dt.name_hits(kos(), "가짜 입꼬리 보톡스 B")
    turn3, _ = tp.turn_conditions([{"table": "procedure", "code": "ZPRC-001", "name": "p"}], hits3, None)
    assert run(d.items(turn3)).codes == ["ZZ-BTX-B", "ZZ-BTX-A"]  # 칸 안 합집합 · 이름 일치 진료항목 먼저(§5-1 ⑤)


def test_intersection_and_one_call_per_args():
    d, h = det()
    r = run(d.items({"concern": [v("concern", "ZZC-A")], "site": [v("site", "ZS-072")]}))
    assert r.codes == ["ZZ-BTX-A"] and r.by["ZZ-BTX-A"] == ["site", "concern"]  # E9
    run(d.items({"concern": [v("concern", "ZZC-A")]}))
    assert sum(1 for t, p in h.requests if t == "concern_candidates" and not p.get("cursor")) == 1  # §5-2 한 번


def test_country_cd6():
    d, _ = det()
    r = run(d.items({"procedure": [v("procedure", "ZPRC-001")], "country": "KR"}))
    assert r.codes == ["ZZ-BTX-A"] and r.country_dropped == 0  # C5 — ZPRD-001 KR
    r2 = run(d.items({"site": [v("site", "ZS-007")], "country": "KR"}))
    assert r2.codes == ["ZZ-BTX-A"] and r2.country_dropped == 1  # 써 LIFT 제품 국가 빈칸 → 뺀 수 1(R7)
    assert run(d.items({"procedure": [v("procedure", "ZPRC-001")], "country": "수입"})).codes == ["ZZ-BTX-B"]


def test_hub_error_drops_that_basis_only():
    hub = fake_hub()
    hub.routes["concern_candidates"] = lambda p: (500, {"detail": "internal: fixture"})
    d, _ = det(hub)
    r = run(d.items({"concern": [v("concern", "ZZC-A")], "procedure": [v("procedure", "ZPRC-001")]}))
    assert r.errors and r.errors[0]["slot"] == "concern"
    assert r.codes == ["ZZ-BTX-A", "ZZ-BTX-B"] and "concern" not in r.per_slot  # 검수 P1-1 — 오류 칸은 교집합에서 뺌
    assert d.errors and d.errors[0]["code"] == "ZZC-A"


def test_hub_error_one_value_of_slot():
    hub = fake_hub()
    hub.routes["concern_candidates"] = lambda p: (
        (500, {"detail": "x"}) if p.get("concern_code") == "ZZC-E2" else cc_route(p)
    )
    d, _ = det(hub)
    r = run(d.items({"concern": [v("concern", "ZZC-A"), v("concern", "ZZC-E2")]}))
    assert r.codes == ["ZZ-BTX-A", "ZZ-BTX-B"] and len(r.errors) == 1  # 오류 값만 그 칸 합집합에서 빠짐


def test_country_asks_only_products_of_other_slots():
    hub = fake_hub()
    d, h = det(hub)
    run(d.items({"procedure": [v("procedure", "ZPRC-001")], "country": "KR"}))
    asked = sorted(p["node"] for t, p in h.requests if t == "facets" and p.get("facet") == "profile")
    assert asked == ["product:ZPRD-001", "product:ZPRD-002"]  # 검수 P2-4 — ZPRD-010 은 안 물음


# ---------- 주제 · 조건(장난감 셈) ----------

U = {  # 값 코드 → 진료항목 집합(장난감)
    "S-BOL": set(range(1, 9)) | {20},
    "C-VOL": set(range(1, 7)),
    "P-FIL": set(range(1, 7)) | {9},
    "R-JUV": {1},
    "C-GIMI": set(range(10, 37)),
    "C-WRK": set(range(40, 61)),
    "S-JAW": {45, 46, 100},
    "C-DHJ": set(range(80, 95)),
    "R-THM": set(range(100, 105)),
    "C-BOLKK": set(),
    "S-CALF": {110},
    "R-JUV2": {1, 2},
}


def V(code: str, table: str | None = None) -> dict:
    t = table or {"S": "site", "C": "concern", "P": "procedure", "R": "product"}[code[0]]
    return {"table": t, "code": code, "name": code}


async def toy(conds: dict) -> list[str]:
    sets = []
    for s in dt.SLOTS:
        if conds.get(s):
            sets.append(set().union(*[U.get(x["code"], set()) for x in conds[s]]))
    return [str(x) for x in sorted(set.intersection(*sets))] if sets else []


def T(*vals: dict) -> tuple[dict, list[str]]:
    return tp.turn_conditions(list(vals), [], None)


def step(topics, active, vals, text, n, llm=None, wd=None):
    turn, order = T(*vals)
    return run(tp.decide(topics, active, turn, order, utterance=text, n=n, llm=llm, withdrawn=wd or [], count=toy))


def test_cd4_withdraw_recounts():
    d1 = step([], None, [V("S-JAW"), V("C-WRK")], "턱 주름", 1)
    turn, order = tp.turn_conditions([], [], None)
    d2 = run(
        tp.decide(
            d1.topics,
            d1.active,
            turn,
            order,
            utterance="턱은 빼 주세요",
            n=2,
            llm=None,
            withdrawn=["site:S-JAW"],
            count=toy,
        )
    )
    assert not d2.topics[0]["conditions"]["site"] and len(d2.topics[0]["items"]) == 21  # E14


# ---------- 한 턴 끝까지(가짜 CLI · 가짜 허브) ----------

EXTRA_ENTRY = [
    {
        "entity_table": "procedure",
        "entity_code": "ZPRC-001",
        "entity_name": "가짜보톡스시술",
        "name_kind": "정식",
        "text": "가짜보톡스시술",
    },
    {
        "entity_table": "procedure",
        "entity_code": "ZPRC-010",
        "entity_name": "가짜리프팅시술",
        "name_kind": "정식",
        "text": "가짜리프팅시술",
    },
    {
        "entity_table": "product",
        "entity_code": "ZPRD-010",
        "entity_name": "가짜기기제품",
        "name_kind": "정식",
        "text": "가짜기기제품",
    },
]


def understand_out(targets, topic=None, country=None) -> str:
    raw = json.dumps(
        {
            "targets": [{"table": t, "code": c, "sure": True} for t, c in targets],
            "kind": "new",
            "sites": [],
            "withdraw": [],
            "kinds": [],
            "out": False,
            "country": country,
            "topic": topic,
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


def test_turns_items_event_ledger_topics_restart(tmp_path, fake_cli):
    hub = state_hub()
    hub.routes["resolve"] = list_route(entry_rows() + EXTRA_ENTRY)
    hub.routes.update({"concern_candidates": hub.routes["concern_candidates"]})
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        (fake_cli[1] / "understand.json").write_text(understand_out([("procedure", "ZPRC-001")]), encoding="utf-8")
        row, kinds, body = r.turn("가짜보톡스시술 궁금해요")
        assert "items" in kinds and kinds.index("items") < kinds.index("done")
        assert row["items"]["codes"] == ["ZZ-BTX-A", "ZZ-BTX-B"]
        assert row["consult"]["active_topic"] == "t1" and row["consult"]["topic"]["by"] == "first"
        assert row["consult"]["conditions"]["procedure"][0]["code"] == "ZPRC-001"
        assert row["consult"]["understand"]["parsed"]["topic"] is None
        (fake_cli[1] / "understand.json").write_text(
            understand_out([("procedure", "ZPRC-010")], topic={"move": "new", "to": None}), encoding="utf-8"
        )
        row2, kinds2, body2 = r.turn(
            "가짜리프팅시술도 궁금"
        )  # 11-23 — 시술은 고르기 묶음: 같은 주제 안 교체(새 주제 아님)
        c = row2["consult"]
        assert c["active_topic"] == "t1" and [t["id"] for t in c["topics"]] == ["t1"]
        assert c["topic"]["llm_move"] == "new" and c["topic"]["move"] == "deepen" and c["topic"]["llm_agree"] is False
        procs = [v["code"] for v in c["topics"][0]["conditions"]["procedure"]]
        assert c["topic"]["conflict"] == [] and procs[0] == "ZPRC-010" and "ZPRC-001" not in procs  # 보톡스 시술 교체됨
        assert row2["items"]["codes"] == ["ZZ-LIFT"]
        assert body2.count('<details class="cs-topic"') == 1  # 테스트 고침 E — 주제 하나여도 머리(교체라 앞 주제 0)
        msg_u = [x for x in r.log.read_text(encoding="utf-8").splitlines() if "--no-session-persistence" in x]
        assert msg_u  # 이해 호출이 돌았다
        page = r.c.get(f"/c/{r.cid}").text
        assert page.count('<details class="cs-topic"') == 1 and 'data-code="ZZ-LIFT"' in page  # 하나여도 머리(고침 E)
    finally:
        r.close()
    app2 = create_app(r.s)  # 재시작 — 마지막 맺는 줄의 topics · active_topic 으로 세운다(TP10)
    page2 = TestClient(app2).get(f"/c/{r.cid}").text
    assert 'data-code="ZZ-LIFT"' in page2 and 'data-code="ZZ-BTX-A"' not in page2  # 재시작 — 교체된 주제 그대로


def test_turn_message_has_topic_block(tmp_path, fake_cli):
    hub = state_hub()
    hub.routes["resolve"] = list_route(entry_rows() + EXTRA_ENTRY)
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        (fake_cli[1] / "understand.json").write_text(understand_out([("procedure", "ZPRC-001")]), encoding="utf-8")
        r.turn("가짜보톡스시술")
        r.turn("가짜보톡스시술 또")
        log = [json.loads(x) for x in r.log.read_text(encoding="utf-8").splitlines()]
        und = [x for x in log if "--no-session-persistence" in x["argv"]]
        assert "# 주제" not in (und[0].get("stdin") or "") and "# 주제" in (und[-1].get("stdin") or "")
        assert "지금 주제: t1 (시술=가짜보톡스시술, 가짜 보톡스) (2)" in (und[-1].get("stdin") or "")  # 칸 묶음
    finally:
        r.close()


def test_d1_blocked_value_leaves_topic_so_next_turn_narrows():
    """D1 (가) — 실물 wp3real000000001 꼴: 「볼 꺼짐 때문에 필러」(고민 볼 꺼짐 = 진료항목 0 → LS3 · 막은 칸 고민) →
    「쥬비덤으로」 는 같은 주제 안 좁히기(막은 고민이 칸에 남아 A = 0 → 새 주제로 튀던 것을 막음)."""
    d1 = step([], None, [V("C-BOLKK"), V("P-FIL"), V("S-BOL")], "볼 꺼짐 때문에 필러", 1)
    assert d1.ls == "LS3" and d1.blocked["slot"] == "concern" and d1.blocked["values"] == ["C-BOLKK"]
    assert d1.topics[0]["conditions"]["concern"] == [] and len(d1.topics[0]["items"]) == 6
    d2 = step(d1.topics, d1.active, [V("R-JUV")], "쥬비덤으로", 2)
    assert d2.active == "t1" and len(d2.topics) == 1 and d2.rec["move"] == "deepen" and d2.topics[0]["items"] == ["1"]
    assert d2.rec["A"] == 1


def test_header_names_deduped():
    from clinic_chat.consult import items_view as iv

    c = {"procedure": [{"table": "name", "code": "item:LDM", "name": "LDM"}], "product": [{"code": "P", "name": "LDM"}]}
    assert iv.cond_names(c) == ["LDM"]  # 실물 LDM — 제품 이름 · 이름 일치가 같은 글자면 한 번


def test_p2_2_first_turn_all_blocked_makes_no_topic():
    U["C-NONE"], U["S-NONE"] = set(), set()
    turn, order = tp.turn_conditions([V("C-NONE"), V("S-NONE")], [], None)
    d = run(tp.decide([], None, turn, order, utterance="종아리 다한증", n=1, llm=None, withdrawn=[], count=toy))
    assert d.blocked["slot"] == "all" and d.topics == [] and d.active is None


def test_p1_1_hub_error_turn_keeps_topic_conditions_and_list(tmp_path, fake_cli):
    hub = state_hub()
    hub.routes["resolve"] = list_route(entry_rows() + EXTRA_ENTRY)
    r = Run(tmp_path, fake_cli, hub=hub)
    try:
        (fake_cli[1] / "understand.json").write_text(understand_out([("procedure", "ZPRC-001")]), encoding="utf-8")
        row1, _, _ = r.turn("가짜보톡스시술")
        hub.routes["concern_candidates"] = lambda p: (500, {"detail": "internal: fixture"})
        (fake_cli[1] / "understand.json").write_text(understand_out([("concern", "ZZC-1")]), encoding="utf-8")
        row2, kinds2, _ = r.turn("가짜고민도 있어요")
        c1, c2 = row1["consult"], row2["consult"]
        assert row2["items"]["hub_error"] is True and row2["items"]["errors"][0]["slot"] == "concern"
        assert c2["topics"] == c1["topics"] and c2["active_topic"] == c1["active_topic"]  # 주제 · 조건 그대로
        assert row2["items"]["codes"] == row1["items"]["codes"] and "items" not in kinds2  # 목록 그대로(조각 없음)
        assert not c2["topics"][0]["conditions"]["concern"] and c2["topic"]["hub_error"] is True
    finally:
        r.close()
