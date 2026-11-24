"""SPEC-011 WP-2 — 2차 읽기 G1 ~ G7(FR 표 줄마다) · 이름만 줄 · 다시 싣기(a578 T5 → T7 꼴)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clinic_chat.consult import fetch_read as fr
from clinic_chat.consult import material as mat
from clinic_chat.consult.ledger import Conv, Ledger
from clinic_chat.llm.chat import ChatConv

SUB_P, SELF = "하위 제품", "자기"
MD = {"procedure_case": {"CASE-IN"}, "procedure": {"PRC-IN"}, "product": {"PRD-IN"}}


def line(key, kinds, *, role=None, holder=None, silver=None, row=None):
    lay = {"role": role, "holder": {"holder_table": holder[0], "holder_code": holder[1], "holder_name": holder[2]}} \
        if holder else None
    return fr.Line(key=key, kinds=kinds, layer=lay, silver_table=(silver or (None, None))[0],
                   silver_id=(silver or (None, None))[1], row=row or {})


def ctx(tables=("procedure",), kinds=(), plan=None, targets=(), q="", **kw):
    plan = plan or fr.Plan()
    return plan, fr.Ctx(targets=set(targets), question=q, md=MD, g2=fr.g2_kinds(list(tables), list(kinds), plan), **kw)


IN_P = ("product", "PRD-IN", "인제품")
OUT_P = ("product", "PRD-OUT", "밖제품")


@pytest.mark.parametrize("fr_id,tables,kinds,plan,lines,want", [
    # FR1 고민 — G2 = KS3 indication · KD9 같은 덧붙임(outcome) 없음
    ("FR1", ["concern"], [], fr.Plan(), [line("a", ["indication"]), line("b", ["outcome"])],
     {"a": "full", "b": "drop"}),
    # FR4 이름만 · 고르기 — KS6 시술 = overview
    ("FR4", ["procedure"], [], fr.Plan(), [line("a", ["overview"]), line("b", ["mechanism"])],
     {"a": "full", "b": "drop"}),
    # FR5 전반 설명 — fetch.kinds 다섯 · lineup names
    ("FR5", ["procedure"], ["overview"], fr.Plan(kinds=["overview", "mechanism", "indication", "outcome", "lineup"],
                                                lineup="names"),
     [line("a", ["mechanism"]), line("b", ["lineup"], role=SUB_P, holder=IN_P), line("c", ["adverse"])],
     {"a": "full", "b": "names", "c": "full"}),
    # FR6 원리
    ("FR6", ["procedure"], ["mechanism"], fr.Plan(kinds=["overview", "mechanism"]),
     [line("a", ["overview"]), line("b", ["outcome"])], {"a": "full", "b": "drop"}),
    # FR8 부작용 — 안전 강제(명동점 밖 하위도 전체 · UD5)
    ("FR8", ["procedure"], ["adverse"], fr.Plan(kinds=["adverse", "recovery"]),
     [line("a", ["adverse"], role=SUB_P, holder=OUT_P), line("b", ["recovery"])], {"a": "full", "b": "full"}),
    # FR10 종류 · 뭐가 있어 — lineup 이름만 · 명동점 밖 하위 = 이름만 · 닿는 하위 = 전체
    ("FR10", ["procedure"], ["lineup"], fr.Plan(kinds=["lineup", "indication"], lineup="names"),
     [line("a", ["indication"], role=SUB_P, holder=OUT_P), line("b", ["indication"], role=SUB_P, holder=IN_P)],
     {"a": "names", "b": "full"}),
    # FR13b 명동점에 X 있나(밖) — scope all → 밖도 전체
    ("FR13b", ["procedure"], ["lineup"], fr.Plan(kinds=["lineup"], scope="all", lineup="names"),
     [line("a", ["indication", "lineup"], role=SUB_P, holder=OUT_P), line("b", ["overview"], role=SUB_P, holder=OUT_P)],
     {"a": "names", "b": "drop"}),
    # FR15 효과 바람 — KS3 outcome
    ("FR15", ["effect"], [], fr.Plan(), [line("a", ["outcome"]), line("b", ["overview"])], {"a": "full", "b": "drop"}),
])
def test_fr_rows(fr_id, tables, kinds, plan, lines, want):
    p, c = ctx(tables, kinds, plan)
    got = {k: v[0] for k, v in fr.decide(lines, p, c).items()}
    assert got == want, fr_id


def test_g3_explicit_by_code_and_name():
    p, c = ctx(["procedure"], [], fr.Plan(), targets=["PRD-OUT"], q="밖제품 어때")
    out = fr.decide([line("a", ["mechanism"], role=SUB_P, holder=OUT_P)], p, c)
    assert out["a"] == ("full", "explicit")  # 종류 · 범위와 무관
    p, c = ctx(["procedure"], [], fr.Plan(), q="밖제품 효과")
    assert fr.decide([line("a", ["mechanism"], role=SUB_P, holder=OUT_P)], p, c)["a"] == ("full", "explicit")


def test_g4_self_and_inherited_never_thinned_and_no_owner_is_full():
    p, c = ctx(["procedure"], ["overview"])
    lines = [line("a", ["overview"], role=SELF, holder=OUT_P), line("b", ["overview"], role="물려받음", holder=OUT_P),
             line("c", ["overview"])]
    assert {k: v[0] for k, v in fr.decide(lines, p, c).items()} == {"a": "full", "b": "full", "c": "full"}


def test_g4_candidate_row_outside_branch_is_names():
    p, c = ctx(["concern"], [])
    ln = line("a", ["indication"], silver=("procedure_case", "CASE-OUT"), row={"name_ko": "밖케이스"})
    ln2 = line("b", ["indication"], silver=("procedure_case", "CASE-IN"), row={"name_ko": "안케이스"})
    out = fr.decide([ln, ln2], p, c)
    assert out["a"] == ("names", "branch") and out["b"][0] == "full"


def test_fr3_topic_turn_g6():
    """FR3 「얼굴에」(about topic) — 고민 칸이 있는 줄은 지금 주제 고민만 · 고민 칸 없는 대상 줄은 주제 진료항목 코드만."""
    plan = fr.Plan(about="topic")
    p, c = ctx(["site"], [], plan, topic_concerns={"탄력 저하"}, topic_codes={"procedure_case:CASE-IN"})
    assert "indication" in c.g2
    lines = [line("a", ["indication"], row={"concern_name": "탄력 저하"}),
             line("b", ["indication"], row={"concern_name": "여드름"}),
             line("c", ["indication"], silver=("procedure_case", "CASE-IN")),
             line("d", ["indication"], silver=("procedure_case", "CASE-OUT"))]
    got = {k: v for k, v in fr.decide(lines, p, c).items()}
    assert got["a"][0] == "full" and got["b"] == ("drop", "topic") and got["c"][0] == "full" and got["d"] == ("drop", "topic")


def test_fr17_fr18_no_lines_nothing():
    p, c = ctx([], [])
    assert fr.decide([], p, c) == {}


# ───── renumber · 이름만 줄 · G7 ─────

def _walk(rows):
    """rows = [(gid, n, body, kinds, role, holder)] — 한 묶음."""
    lines = [{"n": n, "gid": gid, "gold_table": "g_indication", "silver_table": None, "silver_id": None,
              "evidence_kind": None, "kind": kinds, "range": [], "layer": None} for gid, n, _, kinds in rows]
    groups = [{"group": 1, "table": "g_indication", "same": {"target_name": "가짜", "site_name": "얼굴", "x": 1},
               "marks": {"layer": "하위 제품"}, "lines": [r[1] for r in rows],
               "rows": [{"n": n, **body} for _, n, body, _ in rows]}]
    return SimpleNamespace(material=SimpleNamespace(groups=groups, lines=lines), reads=[])


def test_renumber_drop_and_names_only_groups():
    w = _walk([("G1", 1, {"product_name": "인", "text": "긴 설명"}, ["indication"]),
               ("G2", 2, {"product_name": "밖", "text": "긴 설명"}, ["indication"]),
               ("G3", 3, {"product_name": "뺌", "text": "긴 설명"}, ["outcome"])])
    cut = {"G1": ("full", "read"), "G2": ("names", "branch"), "G3": ("drop", "kind")}
    tm = mat.renumber(w, {}, {}, 10, v3=True, cut=cut)
    assert [x["n"] for x in tm.new_lines] == [10, 11]
    full, thin = tm.groups
    assert full["rows"] == [{"n": 10, "product_name": "인", "text": "긴 설명"}] and full["marks"] == {"layer": "하위 제품"}
    assert thin["marks"] == {"names_only": "branch"} and thin["same"] == {"target_name": "가짜", "site_name": "얼굴"}
    assert thin["rows"] == [{"n": 11, "product_name": "밖"}]
    nl = {x["gid"]: x for x in tm.new_lines}
    assert nl["G2"]["names_only"] is True and "names_only" not in nl["G1"] and "G3" not in nl


def _conv():
    return Conv("c1", ChatConv("c1"))


def test_g7_names_then_named_again_same_number_full():
    """a578 T5 → T7 꼴 — 이름만 실린 줄(T5)을 뒤 턴이 이름으로 다시 물으면(G3 명시) 같은 번호로 상세가 실린다."""
    c = _conv()
    body = {"product_name": "슈링크", "text": "긴 설명"}
    w = _walk([("G1", 1, body, ["indication"])])
    tm5 = mat.renumber(w, c.sent, c.summaries, c.next_n, v3=True, cut={"G1": ("names", "branch")},
                       sent_names=c.sent_names)
    Ledger._apply_end(c, {"turn": 5, "error": None, "material": tm5.ledger(), "llm": {}})
    n = tm5.new_lines[0]["n"]
    assert c.sent == {} and c.sent_names == {"G1": n}  # LG6 — 이름만은 `sent` 에 없다
    # 다음 턴도 이름만이면 다시 보내지 않고 그 번호를 seen_lines 로
    tm6 = mat.renumber(w, c.sent, c.summaries, c.next_n, v3=True, cut={"G1": ("names", "branch")},
                       sent_names=c.sent_names)
    assert not tm6.groups and tm6.seen_lines == [n]
    Ledger._apply_end(c, {"turn": 6, "error": None, "material": tm6.ledger(), "llm": {}})
    # 이름으로 다시 물음 → 전체 · 같은 번호
    tm7 = mat.renumber(w, c.sent, c.summaries, c.next_n, v3=True, cut={"G1": ("full", "explicit")},
                       sent_names=c.sent_names)
    assert tm7.groups[0]["rows"] == [{"n": n, **body}] and tm7.next_n == c.next_n  # 새 번호를 쓰지 않음
    Ledger._apply_end(c, {"turn": 7, "error": None, "material": tm7.ledger(), "llm": {}})
    assert c.sent == {"G1": n} and c.sent_names == {}
    tm8 = mat.renumber(w, c.sent, c.summaries, c.next_n, v3=True, cut={"G1": ("full", "read")}, sent_names=c.sent_names)
    assert not tm8.groups and tm8.seen_lines == [n]  # 전체로 실은 뒤는 지금처럼 seen_lines


def test_renumber_without_cut_unchanged():
    w = _walk([("G1", 1, {"product_name": "a"}, ["indication"]), ("G2", 2, {"product_name": "b"}, ["outcome"])])
    a = mat.renumber(w, {}, {}, 1, v3=True)
    assert [len(g["rows"]) for g in a.groups] == [2] and all("names_only" not in x for x in a.new_lines)
