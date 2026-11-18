"""WP-0 · WP-2 — 단계 ① 맨 앞 판단(CD · TG · KD · RG · 입구 고르기) · 모의 Jev · 모의 허브(가짜 이름)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from clinic_chat import prompts
from clinic_chat.config import ROOT
from clinic_chat.walk.front import front
from clinic_chat.walk.kinds import DEFAULTS, KIND_CODES, KIND_NAME, SAFETY_KINDS
from tests.conftest import make_settings
from tests.fake_jev import FakeJev
from tests.walk_fixture import A1, C1, P1, PR, cand, jev_noul, make_hub

P2 = {"entity_table": "procedure", "entity_code": "ZZP-2", "entity_name": "가짜시술 플러스", "text": "가짜시술플러스"}
S1 = {"entity_table": "site", "entity_code": "ZZS-1", "entity_name": "가짜", "text": "가짜"}
COND = {"entity_table": "condition", "entity_code": "ZZN-1", "entity_name": "가짜조건", "text": "가짜조건"}


async def run(tmp_path, hub, noul, **kw):
    s = make_settings(tmp_path, **kw)
    fj = FakeJev(noul_fn=noul)
    return await front("가짜 질문", hub.client(), fj.jev(s), s), fj


def decisions(f):
    return {t["id"]: t["decision"] for t in f.trace_front["targets"]}


def kinds_tr(f):
    return {k["code"]: k for k in f.trace_front["kinds"]}


async def test_named_when_all_target_noul_low_and_no_entry_pick(tmp_path):
    """완료 1 — 대상 Noul 이 전부 no 이하여도 입구 고르기가 돌지 않고 덮이지 않은 일치가 named(TG3 · TG4 — e2e 4)."""
    f, fj = await run(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(target=0.05))
    assert decisions(f) == {"procedure:ZZP-1": "named"} and f.trace_entry_pick is None
    assert [e.node for e in f.entries] == ["procedure:ZZP-1"] and f.entries[0].read_as["by"] == "TG3"
    assert f.trace_front["targets"][0]["noul"] == 0.05  # noul 은 그대로 기록
    assert not any(k.startswith("entry_") for r in fj.requests for k in r["questions"])


async def test_covered_piece_dropped_or_kept(tmp_path):
    """완료 2 — 덮인 조각: no 이하 · 사이 값 → dropped(읽지 않음 · 기록 — 덮인 조각 규칙 11-17) / yes → target."""
    res = [cand(P2), cand(P1, span="가짜시술", covered_by="가짜시술플러스")]
    low = lambda i, st: 0.9 if i == 0 else 0.1  # noqa: E731
    f, _ = await run(tmp_path, make_hub(resolve=res), jev_noul(target=low))
    assert decisions(f) == {"procedure:ZZP-2": "target", "procedure:ZZP-1": "dropped"}
    assert f.trace_front["dropped"][0]["id"] == "procedure:ZZP-1" and [e.node for e in f.entries] == ["procedure:ZZP-2"]
    mid = lambda i, st: 0.9 if i == 0 else 0.5  # noqa: E731
    f2, _ = await run(tmp_path, make_hub(resolve=res), jev_noul(target=mid))
    assert decisions(f2)["procedure:ZZP-1"] == "dropped"  # TG2 「넘김 쪽」 은 덮이지 않은 조각에만
    assert f2.trace_front["dropped"] == [{"id": "procedure:ZZP-1", "noul": 0.5, "covered_by": "가짜시술플러스"}]
    assert [e.node for e in f2.entries] == ["procedure:ZZP-2"]
    hi = lambda i, st: 0.9  # noqa: E731
    f3, _ = await run(tmp_path, make_hub(resolve=res), jev_noul(target=hi))
    assert decisions(f3)["procedure:ZZP-1"] == "target" and not f3.trace_front["dropped"]
    assert {e.node for e in f3.entries} == {"procedure:ZZP-1", "procedure:ZZP-2"}


async def test_uncovered_between_kept_tg2(tmp_path):
    """덮이지 않은 조각의 사이 값은 그대로 TG2 넘김 쪽(uncertain_kept · 표지)."""
    res = [cand(P2), cand(P1, start=10)]
    mid = lambda i, st: 0.9 if i == 0 else 0.5  # noqa: E731
    f, _ = await run(tmp_path, make_hub(resolve=res), jev_noul(target=mid))
    assert decisions(f)["procedure:ZZP-1"] == "uncertain_kept"
    assert next(e for e in f.entries if e.node == "procedure:ZZP-1").read_as["by"] == "TG2"


async def test_short_and_long_both_said_are_both_targets(tmp_path):
    """완료 3 — 짧은 이름과 긴 이름을 둘 다 따로 말함(둘 다 covered_by = null) → 둘 다 대상(Noul 낮아도 named)."""
    res = [cand(P2), cand(P1, start=10)]
    f, _ = await run(tmp_path, make_hub(resolve=res), jev_noul(target=0.1))
    assert {e.node for e in f.entries} == {"procedure:ZZP-1", "procedure:ZZP-2"}


async def test_same_norm_two_tables_split_by_start_table_or_both(tmp_path):
    """완료 4 — 같은 norm 이 두 표에: 시작 표로 갈리면 하나 + overlaps / 안 갈리면 둘 다."""
    res = [cand(C1, "겹친말"), cand(A1, "겹친말")]
    f, _ = await run(tmp_path, make_hub(resolve=res), jev_noul(kinds={"indication": 0.9}))
    assert [e.node for e in f.entries] == ["concern:ZZC-1"] and f.trace_entry["overlaps"][0]["picked"] == ["concern:ZZC-1"]
    assert "adverse" not in f.kinds  # 다른 표로 갈린 낱말로는 KD5 를 더하지 않는다
    f2, _ = await run(tmp_path, make_hub(resolve=res), jev_noul(kinds={"same_day": 0.9}))  # 어느 시작 표에도 없음
    assert {e.node for e in f2.entries} == {"concern:ZZC-1", "adverse:ZZA-1"} and not f2.trace_entry["overlaps"]


async def test_kinds_yes_between_no(tmp_path):
    """완료 5 — yes 둘 = 둘 다 · 전부 사이 = 1위 + 기본 묶음 · 기본 밖 사이 = offered · 전부 no = default · 안전 종류 없음."""
    hub = make_hub(resolve=[cand(P1)], coverage={"procedure:ZZP-1": {"mechanism": (1, 0, 2)}})
    f, _ = await run(tmp_path, hub, jev_noul(kinds={"outcome": 0.9, "mechanism": 0.8}))
    assert {"outcome", "mechanism"} <= set(f.kinds) and f.required == ["mechanism", "outcome"]
    f2, _ = await run(tmp_path, hub, jev_noul(kinds={k: 0.45 for k in KIND_CODES} | {"mechanism": 0.6}))  # KD10 문턱 아래
    assert f2.kinds == ["overview", "mechanism"] and kinds_tr(f2)["mechanism"]["by"] == "top"
    assert kinds_tr(f2)["overview"]["by"] == "default" and f2.required == ["mechanism"]
    off = {o["code"]: o["rows"] for o in f2.offered}
    assert "outcome" in off and kinds_tr(f2)["outcome"]["decision"] == "offered"
    f3, _ = await run(tmp_path, hub, jev_noul(kinds={}))
    assert f3.kinds == ["overview"] and f3.trace_front["kinds_by"] == "default" and f3.required == []
    for ff in (f, f2, f3):
        assert not set(SAFETY_KINDS) & set(ff.kinds)


def test_kd7_safety_kinds_not_in_non_safety_defaults():
    for r in ("procedure", "concern", "attr_start", "combo", "same_day", "sequence", "offer"):
        assert not set(SAFETY_KINDS) & set(DEFAULTS[r]), r


async def test_kd5_condition_entry_reads_contraindication(tmp_path):
    """완료 6 — 대상이 condition 개념이면 Jev 값과 무관하게 contraindication."""
    f, _ = await run(tmp_path, make_hub(resolve=[cand(P1), cand(COND, start=5)]), jev_noul(kinds={"overview": 0.9}))
    assert "contraindication" in f.kinds and kinds_tr(f)["contraindication"]["by"] == "entry"
    assert "avoid" in f.ranges


async def test_one_jev_request_when_candidates_le_32(tmp_path):
    """완료 7 — 후보 1 ~ 32 이면 단계 ① 의 Jev 요청 1 · 33 이상이면 둘째 요청은 target 만."""
    f, fj = await run(tmp_path, make_hub(resolve=[cand(P1), cand(PR, start=4)]), jev_noul())
    assert f.jev_requests == 1 and len(fj.requests) == 1
    q = fj.requests[0]["questions"]
    assert set(q) == {"target_0", "target_1", "gap", *(f"kind_{i}" for i in range(14))}
    many = [cand({**P1, "entity_code": f"ZZP-{i}", "text": f"가짜{i}"}, span=f"가짜{i}", start=i) for i in range(40)]
    f2, fj2 = await run(tmp_path, make_hub(resolve=many), jev_noul())
    assert f2.jev_requests == 2 and all(not k.startswith("kind_") for k in fj2.requests[1]["questions"])
    assert len(f2.trace_front["targets"]) == 40


async def test_zero_candidates_entry_pick_and_out_of_scope(tmp_path):
    """CD4 · TG4 — 후보 0 일 때만 입구 고르기 · 종류 전부 no + gap yes = 범위 밖(입구 고르기 없음)."""
    lst = [{"gold_table": "g_entry", "gid": "GEN-99001", **P1}]
    f, _ = await run(tmp_path, make_hub(resolve=[], list_mode=lst), jev_noul(kinds={"overview": 0.9}, entry=0.9))
    assert f.trace_entry_pick["result"] == "confirmed" and f.entries[0].by == "noul" and not f.end
    f2, _ = await run(tmp_path, make_hub(resolve=[], list_mode=lst), jev_noul(kinds={}, gap=0.9))
    assert f2.end == "범위 밖" and f2.trace_entry_pick is None
    f3, _ = await run(tmp_path, make_hub(resolve=[], list_mode=lst), jev_noul(kinds={"overview": 0.9}, entry=0.5))
    assert f3.end == "입구 없음" and f3.ask_back[0]["id"] == "procedure:ZZP-1"


async def test_gap_yes_with_target_reads_and_sets_scope_gap(tmp_path):
    """RG4 · RG5 — 입구가 있으면 gap yes 여도 끝내지 않고 scope_gap 만."""
    f, _ = await run(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(gap=0.9))
    assert not f.end and f.scope_gap and f.kinds


async def test_candidate_state_has_rows_from_coverage(tmp_path):
    """CD3 · §3-3 — 후보 설명에 coverage 의 0 이 아닌 셈."""
    hub = make_hub(resolve=[cand(PR)], coverage={"product:ZZR-1": {"outcome": (0, 4, 1), "lineup": (0, 0, 0)}})
    f, fj = await run(tmp_path, hub, jev_noul())
    c = fj.requests[0]["state"]["candidates"][0]
    assert c["rows"] == {"outcome": {"lower": 4, "inherited": 1}} and c["id"] == "product:ZZR-1"
    assert f.coverage["product:ZZR-1"]["outcome"]["lower"] == 4


async def test_wp0_instructions_name_inline(tmp_path):
    """WP-0 — 질문 id 마다 그 대상의 이름이 instructions 글 안에(종류 14 · 대상 후보)."""
    f, fj = await run(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul())
    q = fj.requests[0]["questions"]
    for i, k in enumerate(KIND_CODES):
        assert f'"{KIND_NAME[k]}"' in q[f"kind_{i}"]["instructions"]
    assert '"가짜시술"' in q["target_0"]["instructions"] and "procedure:ZZP-1" in q["target_0"]["instructions"]
    assert "{" not in q["target_0"]["instructions"]


def test_wp0_entry_and_candidate_templates_have_name():
    assert "{name}" in prompts.jev("entry") and "{name}" in prompts.jev("candidate")
    assert set(prompts.jev_sections("front")) == {"target", "kind", "gap"}


def test_no_choice_words_and_noul_only():
    """완료 8 — Choice · ask_range · ask_entity · range_confidence 낱말 0 · Jev 응답은 noul 만 읽는다."""
    banned = re.compile(r"\bChoice\b|ask_range|ask_entity|range_confidence|\bparticle\b")
    for p in (Path(ROOT) / "clinic_chat").rglob("*.py"):
        if "experiments" in p.parts or p.name == "jev_client.py":
            continue
        assert not banned.search(p.read_text(encoding="utf-8")), p.name
    for name in ("front.py", "entry.py", "depth.py", "jev_requests.py"):
        src = (Path(ROOT) / "clinic_chat" / "walk" / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "choices" not in attrs, name


MAT = {"entity_table": "material", "entity_code": "ZZM-1", "entity_name": "가짜재료", "text": "가짜재료"}


async def test_w1_no_applicable_kind_falls_back_to_kd6_defaults(tmp_path):
    """코드 검수 v05 W-1 — 읽기로 한 종류가 대상의 표에 전부 없으면(KD4 로 다 건너뜀) KD6 기본 묶음으로 읽는다."""
    f, _ = await run(tmp_path, make_hub(resolve=[cand(S1)]), jev_noul(kinds={"recovery": 0.9}))
    assert f.attr_start and f.kinds == list(DEFAULTS["attr_start"]) and f.trace_front["kinds_by"] == "default"
    kt = kinds_tr(f)
    assert kt["recovery"]["decision"] == "skipped" and kt["recovery"]["by"] == "yes"
    assert all(kt[k]["decision"] == "read" and kt[k]["by"] == "default" for k in f.kinds)
    assert f.required == [] and not f.end
    # 재료 대상 + 하위 목록 yes · 개요 사이 → 개요는 offered 가 아니라 기본 묶음으로 읽는다(재료 표에 적응 조회는 없다)
    f2, _ = await run(tmp_path, make_hub(resolve=[cand(MAT)]), jev_noul(kinds={"lineup": 0.9, "overview": 0.45}))
    assert f2.kinds == ["overview"] and f2.trace_front["kinds_by"] == "default" and f2.offered == []
    assert kinds_tr(f2)["lineup"]["decision"] == "skipped"


# ───── 0.6 — KD9 · KD10 · KD11(SPEC-002 §3-7-2 · T26 · T27) ─────

async def test_t26_kd9_overview_reads_whole_set(tmp_path):
    """T26 — 개요 yes(또는 1위) → 원리 · 어디에 쓰나 · 효과 · 하위 목록이 `read`(by kd9) · 표에 없는 종류는 skipped ·
    required 에 안 든다."""
    for ov in (0.9, 0.6):  # yes · 1위(사이 값)
        f, _ = await run(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(kinds={"overview": ov}))
        kt = kinds_tr(f)
        for k in ("mechanism", "indication", "outcome", "lineup"):
            assert kt[k]["decision"] == "read" and kt[k]["by"] == "kd9", (ov, k)
        assert f.required == ["overview"]
    # 재료(속성) 대상: 개요만 그 표에 있다 → 나머지는 skipped(KD4)
    f, _ = await run(tmp_path, make_hub(resolve=[cand(MAT)]), jev_noul(kinds={"overview": 0.9}))
    kt = kinds_tr(f)
    assert f.kinds == ["overview"] and all(kt[k]["decision"] == "skipped" for k in ("mechanism", "outcome"))


async def test_t27_kd10_threshold_and_kd11(tmp_path):
    """T27 — 사이 값 0.5 이상 = read(kd10) · 아래 = offered / 강한 종류가 있는 범위의 no 이하 기본 묶음 = offered(kd11) ·
    전부 no 면 KD6 그대로 · 설정값을 0.6 으로 바꾸면 경계가 따라 움직인다."""
    noul = jev_noul(kinds={"indication": 0.9, "combo": 0.55, "outcome": 0.45})
    f, _ = await run(tmp_path, make_hub(resolve=[cand(P1)]), noul)
    kt = kinds_tr(f)
    assert kt["combo"]["by"] == "kd10" and kt["combo"]["decision"] == "read" and "combo" not in f.required
    assert kt["outcome"]["decision"] == "offered"
    # 시술 범위(어디에 쓰나 yes)의 기본 묶음 개요(0.1) → KD11 로 offered · 조합 범위(kd10)의 기본 package(0.1)도
    off = {o["code"]: o.get("by") for o in f.offered}
    assert off.get("overview") == "kd11" and off.get("package") == "kd11" and "overview" not in f.kinds
    assert f.trace_front["thresholds"]["kind_read"] == 0.5
    f2, _ = await run(tmp_path, make_hub(resolve=[cand(P1)]), noul, jev_kind_read_at=0.6)
    assert kinds_tr(f2)["combo"]["decision"] == "offered" and "combo" not in f2.kinds
    # 사이 값인 기본 묶음 종류는 지금처럼 읽는다
    f3, _ = await run(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(kinds={"indication": 0.9, "overview": 0.4}))
    assert kinds_tr(f3)["overview"]["by"] == "default"
    # 전부 no → KD6(기본 묶음 그대로 · KD11 안 돎)
    f4, _ = await run(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(kinds={}))
    assert f4.kinds == ["overview"] and f4.trace_front["kinds_by"] == "default" and not f4.offered
