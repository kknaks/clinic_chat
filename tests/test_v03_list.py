"""0.3 WP-6 — 리스트 · 요약 줄 · 내려온 묶음 · SP(SPEC-006 §6 · S6-T15 · T16 · T26 · SP-T1 ~ T5 · SPEC-001 0.7.2 SP ·
코디 W-1 · W-9 (a) · W-10 · B-7 · B-8). 허브 응답은 가짜 코드 · 가짜 이름으로 손으로 적은 꼴."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from clinic_chat.consult import material as mat
from clinic_chat.consult.listing import build_list
from clinic_chat.consult.prompt import load
from clinic_chat.consult.render import TEMPLATES, Render
from clinic_chat.walk.calls import Call
from clinic_chat.walk.verified import site_match_by
from tests.fake_hub import row
from tests.state_fixture import CC, Run, cc_group, jev, state_hub
from tests.v04_fixture import out, set_map

# 0.4(검수 C-3) — 켠 턴의 입구 = 가짜 이해 출력(이번 말 낱말 → 출력 · 기대값은 그대로 — QS5 · CP5)
UMAP = {"가짜부위": out([("concern", "ZZC-1"), ("site", "ZZS-1")], sites=["ZZS-1"], kinds=["indication"]),
        "가짜고민": out([("concern", "ZZC-1")], kinds=["indication"]), "*": out([])}


def _run(tmp_path, fake_cli, **kw):
    set_map(fake_cli[1], UMAP)
    return Run(tmp_path, fake_cli, **kw)


ROOT = Path(__file__).resolve().parent.parent
SLOTS = load(ROOT / "prompts" / "consult.json").slots
HANGUL = re.compile(r"[가-힣]")


def grp(pcode, pname, gid, *, prefer=None, n_prefer=None):
    g = cc_group("ZZC-1", "가짜고민", "procedure", pcode, pname, gid)
    if prefer is not None:
        g["rows"][0]["site_prefer"] = prefer
        if prefer:
            g["rows"][0]["site_match"] = [{"by": "column"}]
    if n_prefer is not None:
        g["n_site_prefer"] = n_prefer
    return g


def desc(mcode, mname, procs, gids):
    return {"modality_code": mcode, "modality_name": mname, "unit": "계열",
            "procedures": [{"procedure_code": c, "procedure_name": n, "scope_gid": f"GSC-{c}", "in_groups": ig}
                           for c, n, ig in procs],
            "from": [{"gid": g, "reach": "패키지 — 항목 코드 없음", "lands": "modality",
                      "path": [{"table": "package", "code": "ZZK-9", "name": "가짜메뉴이름", "role": None,
                                "scope_gid": None}]} for g in gids]}


def resp(groups, *, applied=None, descended=None, unreached=None, prefer_rows=None):
    body = {"groups": groups, "modality_only": [], "unreached": unreached or [], "inherited_from": None,
            "counts": {"rows": sum(len(g["rows"]) for g in groups)}}
    if applied is not None:
        body["site_applied"] = applied
    if prefer_rows is not None:
        body["counts"]["site_prefer"] = {"rows": prefer_rows, "groups": prefer_rows}
    if descended is not None:
        body["descended"] = descended
        body["counts"]["descended"] = {"undescended": 0}
    return body


MODS = {"ZZP-1": ("ZZM-1", "가짜계열"), "ZZP-2": ("ZZM-2", "가짜계열둘"), "ZZP-3": ("ZZM-3", "가짜계열셋"),
        "ZZP-4": ("ZZM-3", "가짜계열셋")}


def reads(*bodies, site=None):
    out = []
    for b in bodies:
        req = {"concern_code": "ZZC-1", **({"site": site} if site else {})}
        out.append(SimpleNamespace(call=Call("concern_candidates", req, ("indication",), ("concern",)),
                                   ra=SimpleNamespace(as_response=lambda b=b: b)))
        for g in b["groups"]:
            node = f"{g['candidate_table']}:{g['candidate_code']}"
            mc, mn = MODS[g["procedure_code"]]
            prof = {"rows": [row("g_profile", f"GPF-{g['procedure_code']}", subject_table="procedure",
                                 subject_code=g["procedure_code"], modality_code=mc, modality_name=mn,
                                 mechanism_name="가짜원리")]}
            out.append(SimpleNamespace(call=Call("facets", {"node": node, "facet": "profile"}, (), (), "read",
                                                 meta={"followup": True, "from_group": node}),
                                       ra=SimpleNamespace(as_response=lambda p=prof: p)))
    return out


def concern(lst):
    return lst["concerns"][0]


def procs(c):
    return [p["procedure_code"] for m in c["modalities"] for p in m["procedures"]]


UNREACHED = [row("g_indication", "GIN-99901", candidate_name="가짜메뉴이름", reach="패키지 — 항목 코드 없음",
                 concern_code="ZZC-1", asked_concern_code="ZZC-1")]


# ───── S6-T26 내려온 묶음의 폭(LS4-1 · W-9 (a)) ─────

def test_s6_t26_descended_width():
    d = [desc("ZZM-1", "가짜계열", [("ZZP-1", "가짜시술", True), ("ZZP-7", "가짜시술칠", False)], ["GIN-99901"]),
         desc("ZZM-3", "가짜계열셋", [("ZZP-3", "가짜시술셋", False), ("ZZP-4", "가짜시술넷", False)], ["GIN-99901"])]
    c = concern(build_list(reads(resp([grp("ZZP-1", "가짜시술", "GIN-91001")], descended=d, unreached=UNREACHED)),
                           {"ZZC-1": "가짜고민"}, codes=True))
    assert procs(c) == ["ZZP-1"]  # ① in_groups 시술은 다시 안 그림
    assert [(m["modality_code"], [p["procedure_code"] for p in m["procedures"]]) for m in c["descended"]] == \
        [("ZZM-3", ["ZZP-3", "ZZP-4"])]  # ② 시술 0 인 계열만 전부
    assert c["hidden"]["descended_n"] == 1  # ③ 직접 닿은 계열의 내려온 나머지는 수만
    m = c["descended"][0]
    assert m["modality_level"] is True and m["from_gids"] == ["GIN-99901"] and all(p["descended"] for p in m["procedures"])
    assert c["hidden"]["unreached_n"] == 1 and c["hidden"]["names"] == ["가짜메뉴이름"]


# ───── S6-T15 리스트 조각 ─────

def test_s6_t15_fragment_text_is_names_only():
    d = [desc("ZZM-3", "가짜계열셋", [("ZZP-3", "가짜시술셋", False)], ["GIN-99901"])]
    lst = build_list(reads(resp([grp("ZZP-1", "가짜시술", "GIN-91001")], descended=d, unreached=UNREACHED)),
                     {"ZZC-1": "가짜고민"}, codes=True)
    out = Render(slots=SLOTS).list(lst)
    text = [t.strip() for t in re.sub(r"<[^>]+>", "\n", out).split("\n") if t.strip()]
    assert text == ["가짜고민", "가짜계열", "가짜시술", "가짜계열셋 계열 시술", "가짜시술셋"]  # 고민 · 계열 · 시술 이름 + W-10
    assert "가짜메뉴이름" not in out and "시술 직접" not in out.replace('data-reach="시술 직접"', "")
    assert 'data-reach="시술 직접"' in out and 'data-concern-match="직접"' in out
    assert 'data-modality-level' in out and 'data-unit="계열"' in out and 'data-descended' in out
    assert 'data-slot="list-section-unreached"' not in out and 'data-slot="list-section-modality-only"' not in out
    assert 'data-slot="mark-modality-level">가짜계열셋 계열 시술<' in out


def test_s6_t15_off_list_unchanged_shape():
    lst = build_list(reads(resp([grp("ZZP-1", "가짜시술", "GIN-91001")], unreached=UNREACHED)), {"ZZC-1": "x"})
    c = concern(lst)
    assert "hidden" not in c and "descended" not in c
    out = Render().list(lst)
    assert 'data-slot="list-section-unreached"' in out and "가짜메뉴이름" in out  # 끈 상태 = 0.1 그대로


def test_s6_t15_turn_ledger_hidden_and_fragment(tmp_path, fake_cli):
    hub = state_hub()
    base = hub.routes["concern_candidates"]

    def cc(p):
        b = base(p)
        if p.get("concern_code") == "ZZC-1":
            b["unreached"] = UNREACHED
            b["descended"] = [desc("ZZM-3", "가짜계열셋", [("ZZP-3", "가짜시술셋", False)], ["GIN-99901"])]
        return b
    hub.routes["concern_candidates"] = cc
    r = _run(tmp_path, fake_cli, hub=hub, noul=jev(kinds={"indication": 0.9}))
    try:
        row1, kinds, body = r.turn("가짜고민 시술")
        c = row1["list"]["concerns"][0]
        assert c["hidden"]["names"] == ["가짜메뉴이름"] and c["descended"][0]["modality_level"] is True
        frag = body.split("event: list\n", 1)[1].split("\n\n", 1)[0]
        assert "가짜메뉴이름" not in frag and "가짜계열셋 계열 시술" in frag
    finally:
        r.close()


# ───── S6-T16 요약 줄 ─────

def test_s6_t16_summary_lines_without_reach():
    d = [desc("ZZM-3", "가짜계열셋", [("ZZP-3", "가짜시술셋", False), ("ZZP-4", "가짜시술넷", False)], ["GIN-99901"])]
    rs = reads(resp([grp("ZZP-1", "가짜시술", "GIN-91001")], descended=d, unreached=UNREACHED))
    only = {"GIN-91001", "GPF-ZZP-1", "GIN-99901"}
    s1 = mat.summaries(rs, only, v3=True)[0]["lines"]
    level = [x for x in s1 if x["modality_level"]]
    assert [(x["modality_name"], x["procedures"], x["gids"]) for x in level] == [("가짜계열셋", 2, ["GIN-99901"])]
    w = SimpleNamespace(material=SimpleNamespace(groups=[], lines=[]), reads=rs)
    import clinic_chat.consult.material as m2
    orig = m2.summary_only_gids
    m2.summary_only_gids = lambda reads: only
    try:
        tm = mat.renumber(w, {}, {}, 1, v3=True)
        tm1 = mat.renumber(w, {}, {}, 1)
    finally:
        m2.summary_only_gids = orig
    rows3 = tm.summary_groups[0]["rows"]
    assert all("reach" not in x and "concern_match" not in x and "modality_level" in x for x in rows3)
    assert all("reach" in x for x in tm1.summary_groups[0]["rows"])  # 끈 상태 = 0.1 꼴
    assert any(x.get("gids") == ["GIN-99901"] for x in tm.new_lines)  # 근거 gid 는 장부에 그대로


# ───── SP-T1 ~ T5(SPEC-001 0.7.2 · 코디 W-1) ─────

def _four(applied, marks):
    gs = [grp("ZZP-1", "가짜시술", "GIN-1", prefer=marks[0], n_prefer=int(marks[0])),
          grp("ZZP-2", "가짜시술둘", "GIN-2", prefer=marks[1], n_prefer=int(marks[1])),
          grp("ZZP-3", "가짜시술셋", "GIN-3", prefer=marks[2], n_prefer=int(marks[2])),
          grp("ZZP-4", "가짜시술넷", "GIN-4", prefer=marks[3], n_prefer=int(marks[3]))]
    return resp(gs, applied=applied, prefer_rows=sum(marks))


def test_sp_t1_prefer_marks_first_stable():
    lst = build_list(reads(_four("prefer", [False, True, False, True]), site="가짜부위"), {}, codes=True)
    c = concern(lst)
    assert [m["modality_code"] for m in c["modalities"]] == ["ZZM-2", "ZZM-3", "ZZM-1"]  # 표지 계열이 앞 · 안정 정렬
    assert procs(c) == ["ZZP-2", "ZZP-4", "ZZP-3", "ZZP-1"]  # 넷 전부 · 묶음 안 표지 시술이 앞
    assert c["site"] == {"applied": "prefer", "prefer_rows": 2, "prefer_groups": 2}
    out = Render().list(lst)
    assert out.count("<li class=\"cs-proc\"") == 4 and len(re.findall(r'<li[^>]*data-site-prefer', out)) == 2
    assert 'data-slot="mark-site-prefer"></span>' in out  # 문구 0(빈 자리)


def test_sp_t2_prefer_zero_marks_keeps_hub_order_no_text():
    lst = build_list(reads(_four("prefer", [False] * 4), site="가짜부위"), {}, codes=True)
    c = concern(lst)
    assert procs(c) == ["ZZP-1", "ZZP-2", "ZZP-3", "ZZP-4"] and c["site"]["prefer_rows"] == 0
    out = Render().list(lst)
    assert "data-site-prefer" not in out and "mark-site-prefer" not in out


def test_sp_t3_filter_and_old_hub_same_list():
    a = concern(build_list(reads(_four("filter", [False] * 4), site="가짜부위"), {}, codes=True))
    b = concern(build_list(reads(_four(None, [False] * 4), site="가짜부위"), {}, codes=True))
    assert a["modalities"] == b["modalities"] and procs(a) == ["ZZP-1", "ZZP-2", "ZZP-3", "ZZP-4"]
    assert a["site"] == {"applied": "filter", "prefer_rows": None, "prefer_groups": None} and b["site"] is None
    assert not any("site_prefer" in p for m in a["modalities"] for p in m["procedures"])


def test_sp_t5_sited_concern_outside_site_is_prefer_and_mixed():
    # 0.7.2 — 부위 있는 고민 + 그 고민 부위 밖의 부위 = prefer(앱은 응답 머리만 읽는다)
    c = concern(build_list(reads(_four("prefer", [True, False, False, False]), site="가짜바깥"), {}, codes=True))
    assert c["site"]["applied"] == "prefer" and procs(c)[0] == "ZZP-1"
    # 한 턴에 filter(안 부위) · prefer(밖 부위)가 섞임 → prefer 로 그리고 filter 응답의 행은 표지 행(코디 W-1)
    f = resp([grp("ZZP-3", "가짜시술셋", "GIN-3")], applied="filter")
    p = _four("prefer", [False, True, False, False])
    rs = reads(f, site="가짜안") + reads(p, site="가짜바깥")
    c2 = concern(build_list(rs, {}, codes=True))
    assert c2["site"]["applied"] == "prefer" and c2["site"]["prefer_rows"] == 2
    marked = {p["procedure_code"] for m in c2["modalities"] for p in m["procedures"] if p["site_prefer"]}
    assert marked == {"ZZP-2", "ZZP-3"} and sorted(procs(c2)) == ["ZZP-1", "ZZP-2", "ZZP-3", "ZZP-4"]


def test_sp_t4_walk_material_site_match_only_on_marked_rows():
    p = _four("prefer", [True, False, False, False])
    rows = [g["rows"][0] for g in p["groups"]]
    assert [bool(site_match_by(r)) for r in rows] == [True, False, False, False]  # 걷기 재료 표지 = 표지 행에만


def test_sp_turn_site_view_and_ledger(tmp_path, fake_cli):
    hub = state_hub()
    base = hub.routes["concern_candidates"]

    def cc(p):
        b = base(p)
        if p.get("site"):
            b["site_applied"] = "prefer"
            b["counts"]["site_prefer"] = {"rows": 0, "groups": 0}
            for g in b["groups"]:
                g["n_site_prefer"] = 0
                for x in g["rows"]:
                    x["site_prefer"] = False
        return b
    hub.routes["concern_candidates"] = cc
    r = _run(tmp_path, fake_cli, hub=hub, noul=jev(kinds={"indication": 0.9}))
    try:
        row1, _, _ = r.turn("가짜부위 에 가짜고민 시술")
        c = row1["list"]["concerns"][0]
        assert c["site"] == {"applied": "prefer", "prefer_rows": 0, "prefer_groups": 0, "name": "가짜부위"}
        j = json.loads(row1["llm_user_message"].split("\n")[1])
        assert j["site"] == {"name": "가짜부위", "applied": "prefer", "prefer_n": 0}  # SP-5 · B-7
        assert not any("site_mode" in (q.get("request") or {}) for q in row1["consult"]["queries"])  # SP-1
    finally:
        r.close()
    assert CC["ZZC-1"]  # 고정 시나리오를 고치지 않았다


def test_s6_t23_list_v3_template_no_hangul():
    body = re.sub(r"(?s)\{#.*?#\}", "", (TEMPLATES / "partials" / "list_v3.html").read_text(encoding="utf-8"))
    assert not HANGUL.search(body)
