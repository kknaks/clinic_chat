"""WP-6 — 시술 리스트(T9 · T23 · PL1 ~ PL9) · 모의 허브."""

from __future__ import annotations

from clinic_chat.clients.hub_rest import ReadAll
from clinic_chat.consult.listing import build_list
from clinic_chat.consult.render import Render
from clinic_chat.walk.breadth import Read
from clinic_chat.walk.calls import Call
from tests.consult_fixture import GROUPS, MOD_ONLY, PROFILES, UNREACHED
from tests.test_consult_material import _walk


async def test_t9_rows_modalities_reach_and_order(tmp_path):
    w = await _walk(tmp_path, "가짜고민 시술", {"indication": 0.9})
    lst = build_list(w.reads, {"ZZC-1": "가짜고민"})
    c = lst["concerns"][0]
    assert c["concern_name"] == "가짜고민" and c["inherited_from"] is None
    assert [m["modality_name"] for m in c["modalities"]] == ["가짜계열A", "가짜계열B"]  # 처음 나온 순서
    p1 = c["modalities"][0]["procedures"][0]
    assert p1["procedure_code"] == "ZZP-1" and p1["reach"] == ["시술 직접", "제품 → 시술"]  # 층 합집합
    assert p1["gids"] == ["GIN-95001", "GIN-95003"]  # 행마다 gid
    procs = [p["procedure_code"] for m in c["modalities"] for p in m["procedures"]]
    assert procs == ["ZZP-1", "ZZP-2"]  # 케이스 · 제품 후보도 시술 이름으로 한 행
    assert c["modality_only"] == [{"modality_name": "가짜계열C", "gids": ["GIN-95004"]}]
    assert c["unreached"] == [{"candidate_name": "가짜제품없음", "reach": "제품 — 케이스 없음", "gid": "GIN-95005"}]
    html = Render().list(lst)
    assert html.count("data-procedure-code=") == 2 and "가짜계열C" in html and "가짜제품없음" in html
    assert 'data-slot="list-section-modality-only"' in html and 'data-slot="list-section-unreached"' in html


def _read(tool, req, resp, meta=None):
    ra = ReadAll(tool, req, complete=True, head={k: v for k, v in resp.items() if k not in ("groups",)},
                 items=resp.get("groups") or resp.get("rows") or [])
    c = Call(tool, req, (), ())
    c.meta.update(meta or {})
    return Read(c, ra, [])


def test_t23_two_reads_same_concern_one_list_no_dup():
    full = {"groups": GROUPS, "modality_only": MOD_ONLY, "unreached": UNREACHED, "inherited_from": None}
    narrowed = {"groups": GROUPS[1:2], "modality_only": [], "unreached": [], "inherited_from": None}
    reads = [_read("concern_candidates", {"concern_code": "ZZC-1", "site": "가짜부위"}, narrowed),
             _read("concern_candidates", {"concern_code": "ZZC-1"}, full)]
    reads += [_read("facets", {"facet": "profile", "node": n}, {"rows": [r]}, {"followup": True, "from_group": n})
              for n, r in PROFILES.items()]
    lst = build_list(reads)
    assert len(lst["concerns"]) == 1
    c = lst["concerns"][0]
    gids = [g for m in c["modalities"] for p in m["procedures"] for g in p["gids"]]
    assert sorted(gids) == ["GIN-95001", "GIN-95002", "GIN-95003"] and len(gids) == len(set(gids))
    assert [m["modality_name"] for m in c["modalities"]] == ["가짜계열B", "가짜계열A"]  # 처음 나온 순서(좁힌 조회 먼저)


def test_pl8_no_cc_read_no_list_and_pl6_inherited():
    assert build_list([]) is None
    up = [{**g, "rows": [{**r, "asked_concern_name": "가짜윗고민"} for r in g["rows"]]} for g in GROUPS[:1]]
    resp = {"groups": up, "modality_only": [], "unreached": [], "inherited_from": {"concern_code": "ZZC-0", "steps": 1}}
    lst = build_list([_read("concern_candidates", {"concern_code": "ZZC-1"}, resp)])
    assert lst["concerns"][0]["inherited_from"] == {"concern_code": "ZZC-0", "concern_name": "가짜윗고민", "steps": 1}
    assert lst["concerns"][0]["modalities"][0]["modality_name"] == ""  # profile 없음 — 이름을 짓지 않는다
