"""0.3 WP-4 — 끝맺음 하나 · 없음 · 꼬리 `close:`(SPEC-006 §4 · §5 · S6-T10 · T11 · T14 · 코디 W-5 · W-8)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from clinic_chat.columns.col_b import TAIL_MARK
from clinic_chat.consult import close as cl
from clinic_chat.consult.prompt import load
from clinic_chat.consult.tail import split_close, split_tail
from clinic_chat.walk.calls import Call, Entry
from tests.conftest import stream
from tests.state_fixture import Run, jev
from tests.v04_fixture import out, set_map

# 0.4(검수 C-3) — 켠 턴의 입구 = 가짜 이해 출력(이번 말 낱말 → 출력 · 기대값은 그대로 — QS5 · CP5)
UMAP = {"가짜제품": out([("product", "ZZR-1")], kinds=["overview"]), "*": out([])}


def _run(tmp_path, fake_cli, **kw):
    set_map(fake_cli[1], UMAP)
    return Run(tmp_path, fake_cli, **kw)


ROOT = Path(__file__).resolve().parent.parent
LABELS = load(ROOT / "prompts" / "consult.json").kind_labels
HANGUL = re.compile(r"[가-힣]")


def _read(node: str, kind: str, total: int, *, complete: bool = True, purpose: str = "read", tool: str = "facets"):
    t, _, c = node.partition(":")
    call = Call(tool, {"node": node} if tool != "pair" else {"a": node}, (kind,), (), purpose)
    return SimpleNamespace(call=call, ra=SimpleNamespace(total=total, complete=complete, error=None))


def _w(reads, *, kinds=("adverse", "overview"), end_state="근거 충족", error=None):
    f = SimpleNamespace(entries=[Entry("procedure", "ZZP-1", "가짜시술", "target")], kinds=list(kinds), offered=[])
    return SimpleNamespace(front=f, reads=reads, end_state=end_state, error=error)


# ───── S6-T10 close 고르기 ─────

def test_s6_t10_pick_order_and_safety():
    ask = {"slot": "concern"}
    emp = [{"about": {"table": "procedure", "code": "P", "name": "x"}, "what": "combo"}]
    alts = [{"table": "modality", "code": "M2", "name": "m2"}]
    assert cl.pick(ask, emp, alts, ["overview"])["by"] == "ask"  # ① ask 가 있으면 언제나
    c = cl.pick(None, emp, alts, ["overview"])
    assert c["by"] == "alt" and c["alt"] == alts and c["offered"] == []
    assert cl.pick(None, emp, [], ["adverse", "package"]) == {"by": "offered", "alt": [], "offered": ["package"],
                                                              "said": None}  # CS4 — 안전 종류 빼고
    assert cl.pick(None, [], alts, ["contraindication", "avoid"])["by"] is None
    assert cl.pick(None, emp, alts, []) == cl.pick(None, emp, alts, [])  # 결정론


def test_em3_alt_excludes_target_modality_in_hub_order_with_limit():
    lst = {"concerns": [{"concern_code": "C1", "modalities": [
        {"modality_code": "M1", "modality_name": "a"}, {"modality_code": "M2", "modality_name": "b"},
        {"modality_code": "M3", "modality_name": "c"}, {"modality_code": "", "modality_name": "z"}],
        "descended": [{"modality_code": "M4", "modality_name": "d"}]}]}
    assert [a["code"] for a in cl.alt(lst, "C1", {"M2"}, 3)] == ["M1", "M3", "M4"]
    assert [a["code"] for a in cl.alt(lst, "C1", set(), 2)] == ["M1", "M2"]
    assert cl.alt(lst, None, set(), 3) == [] and cl.alt(lst, "C9", set(), 3) == []


def test_close_view_names_only():
    c = cl.pick(None, [], [], ["package", "combo"])
    assert cl.close_view(c, None, LABELS) == {"by": "offered", "offered": [LABELS["package"], LABELS["combo"]]}
    a = cl.pick(None, [{"x": 1}], [{"table": "modality", "code": "M1", "name": "nm"}], [])
    assert cl.close_view(a, None, LABELS) == {"by": "alt", "alt": ["nm"]}


# ───── S6-T14 empty ─────

def test_s6_t14_complete_zero_is_empty_and_safety_flag():
    w = _w([_read("procedure:ZZP-1", "adverse", 0), _read("procedure:ZZP-1", "overview", 3)])
    e = cl.empties(w, {"procedure:ZZP-1": "가짜시술"})
    assert [(x["about"]["code"], x["what"], x["total"], x["complete"]) for x in e] == [("ZZP-1", "adverse", 0, True)]
    v = cl.view(e, LABELS)
    assert v == [{"about": "가짜시술", "what": LABELS["adverse"], "safety": True}]  # W-5
    assert cl.view([{**e[0], "what": "combo"}], LABELS)[0]["safety"] is False


def test_s6_t14_error_incomplete_aborted_not_empty():
    assert cl.empties(_w([_read("procedure:ZZP-1", "adverse", 0, complete=False)]), {}) == []
    assert cl.empties(_w([_read("procedure:ZZP-1", "adverse", 0)], end_state="실행 중단"), {}) == []
    assert cl.empties(_w([_read("procedure:ZZP-1", "adverse", 0)], error={"kind": "x"}), {}) == []
    # 두 조회로 읽은 종류는 합이 0 일 때만
    assert cl.empties(_w([_read("procedure:ZZP-1", "adverse", 0), _read("procedure:ZZP-1", "adverse", 2)]), {}) == []
    # 읽지 않은 종류는 empty 아님
    assert cl.empties(_w([], kinds=("combo",)), {}) == []


def test_s6_t14_concern_candidates_zero_groups_with_unreached_not_empty():
    r = _read("concern:ZZC-1", "indication", 0, tool="concern_candidates")
    r.ra.as_response = lambda: {"groups": [], "unreached": [{"gid": "GIN-1"}], "descended": [{"modality_code": "M"}]}
    w = _w([r], kinds=("indication",))
    w.front.entries = [Entry("concern", "ZZC-1", "가짜고민", "target")]
    assert cl.empties(w, {}) == []  # E6 실측에서 찾은 것 — total(묶음 수) 0 이어도 다른 행이 있으면 없음 아님
    r.ra.as_response = lambda: {"groups": [], "unreached": [], "modality_only": []}
    assert [x["what"] for x in cl.empties(w, {})] == ["indication"]


def test_em5_synergy_zero_only_when_combo_asked():
    obj = {"subjects": [{"table": "procedure", "code": "ZZP-1", "name": "가짜시술"}]}
    q = [{"rule": "QY4", "request": {"a": "procedure:ZZP-1"}, "total": 0}]
    assert cl.synergy_empty(obj, q, ["overview"]) == []
    e = cl.synergy_empty(obj, q, ["combo"])
    assert [(x["about"]["name"], x["what"]) for x in e] == [("가짜시술", "combo")]
    assert cl.synergy_empty(obj, [{**q[0], "total": 2}], ["combo"]) == []


def test_em6_list_zero_concern():
    lst = {"concerns": [{"concern_code": "C1", "concern_name": "c", "modalities": [], "inherited_from": None},
                        {"concern_code": "C2", "concern_name": "d", "modalities": [], "inherited_from": {"x": 1}},
                        {"concern_code": "C3", "concern_name": "e", "modalities": [{"procedures": [{"p": 1}]}]}]}
    assert [(x["about"]["code"], x["what"]) for x in cl.list_empty(lst)] == [("C1", cl.EM6_WHAT)]
    assert LABELS[cl.EM6_WHAT]


# ───── S6-T11 꼬리 close: ─────

def test_s6_t11_split_close():
    t = "답 한 줄\n물음?\n" + TAIL_MARK + "\n1: 3\nclose: 2\n"
    rest, said = split_close(t, TAIL_MARK)
    assert said == 2 and "close" not in rest
    assert split_tail(rest, TAIL_MARK)[1] == [{"answer_line": 1, "rows": [3]}]  # 0.1 꼬리는 그대로 읽힌다
    assert split_close("본문\n" + TAIL_MARK + "\n1: 1\n", TAIL_MARK)[1] is None
    assert split_close("본문만", TAIL_MARK) == ("본문만", None)
    assert split_close("a\n" + TAIL_MARK + "\nclose: 0\n", TAIL_MARK)[1] == 0


def _answer(fake_cli, close_line: str | None):
    tail = "1: 1\n" + (close_line + "\n" if close_line else "")
    (fake_cli[1] / "answer.jsonl").write_text(stream("짧은 답\n어떠세요?\n" + TAIL_MARK + "\n" + tail), encoding="utf-8")


def test_s6_t11_said_recorded_and_ask_not_counted_when_zero(tmp_path, fake_cli):
    r = _run(tmp_path, fake_cli, noul=jev({"가짜제품": 0.9}, kinds={"overview": 0.9}))
    try:
        _answer(fake_cli, "close: 0")
        row1, _, body = r.turn("가짜제품 받고 싶어요")
        c = row1["consult"]
        assert c["close"]["by"] == "ask" and c["close"]["said"] == 0
        assert c["after"]["asked"] == [] and not any(d["op"] == "asked" and d["why"] != "answered"
                                                     for d in c["diff"])  # W-8 — 준 횟수 · 걸린 선택지에 없다
        assert "close: 0" not in body and "close:" not in row1["answer"]  # 화면 · 본문에 꼬리 글자 0
        assert row1["answer_lines"] == [{"answer_line": 1, "rows": [1]}]
        _answer(fake_cli, "close: 2")
        row2, _, _ = r.turn("가짜제품 또 알려줘")
        assert row2["consult"]["close"]["said"] == 2
        assert len(row2["consult"]["after"]["asked"]) == (1 if row2["consult"]["close"]["by"] == "ask" else 0)
    finally:
        r.close()


def test_s6_t11_no_close_line_is_null_and_counted(tmp_path, fake_cli):
    r = _run(tmp_path, fake_cli, noul=jev({"가짜제품": 0.9}, kinds={"overview": 0.9}))
    try:
        _answer(fake_cli, None)
        row, _, _ = r.turn("가짜제품 받고 싶어요")
        assert row["consult"]["close"]["by"] == "ask" and row["consult"]["close"]["said"] is None
        assert len(row["consult"]["after"]["asked"]) == 1  # null 은 센다(지금 동작)
    finally:
        r.close()


def test_s6_t10_turn_message_has_close_block(tmp_path, fake_cli):
    r = _run(tmp_path, fake_cli, noul=jev({"가짜제품": 0.9}, kinds={"overview": 0.9}))
    try:
        row, _, _ = r.turn("가짜제품 받고 싶어요")
        msg = row["llm_user_message"]
        blk = json.loads(msg.split("### 끝맺음\n", 1)[1].split("\n", 1)[0])
        assert blk["by"] == "ask" and blk["ask"]["options"] and "ZZC-" not in json.dumps(blk, ensure_ascii=False)
    finally:
        r.close()


def test_s6_t23_close_py_no_hangul():
    body = re.sub(r'(?s)""".*?"""', "", (ROOT / "clinic_chat" / "consult" / "close.py").read_text(encoding="utf-8"))
    body = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    assert not HANGUL.search(body)
