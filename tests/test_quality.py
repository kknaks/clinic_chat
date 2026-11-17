"""WP-7 — D1 ~ D9(합성) · 가린 채점 입력 · 키 거부 · 합성 키 배관 · 보고서(모의 CLI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clinic_chat.config import ROOT
from clinic_chat.quality import checks, judge
from clinic_chat.quality.checks import NA
from clinic_chat.quality.grade import PLUMBING_MARK, KeyRejected, accept_key, plumbing, synthetic_inputs
from clinic_chat.report import render
from tests.conftest import make_settings, stream

SYN = synthetic_inputs()
GOLD, KEY, KR = SYN["gold"], SYN["key"], SYN["key_rows"]
TRACE_B = {"verified_gids": [{"gid": "GIN-90001"}, {"gid": "GIN-90002"}]}


def run(col="b", answer="", cited=None, trace=None, key=None, kr=None, **kw):
    return checks.run(column=col, answer=answer, cited=cited or [], trace=trace if trace is not None else TRACE_B,
                      key=key or KEY, key_rows=kr or KR, gold=GOLD, **kw)


GOOD = "가짜고민베타에는 가짜시술알파 · 가짜시술감마가 있다 [GIN-90001] [GIN-90002]"


def test_d1_d2():
    assert run(answer=GOOD, cited=["GIN-90001"])["D1"]["violations"] == []
    assert run(answer=GOOD, cited=["GIN-99999"])["D1"]["violations"] == ["GIN-99999"]
    assert run(cited=["GIN-90001"], trace={"verified_gids": []})["D2"]["violations"] == ["GIN-90001"]
    a_tr = {"tool_calls": [{"tool": "raw_get", "paths": ["f.a"], "truncated": True},
                           {"tool": "raw_search", "paths": ["f.b"], "truncated": False}]}
    r = run("a", cited=["f.a", "f.a.x", "f.b[0]"], trace=a_tr, raw_exists=lambda p: True)
    assert r["D2"]["violations"] == ["f.a.x"] and r["D1"]["violations"] == []


def test_d3_missing_invented_extra_and_m1():
    ok = run(answer=GOOD)
    assert ok["D3"]["missing"] == [] and ok["D3"]["invented"] == [] and ok["M1"]["missing"] == 0
    bad = run(answer="가짜고민베타에는 가짜시술알파 ZZQ-999 만")
    assert bad["D3"]["missing"] == ["procedure:ZZP-902"] and bad["D3"]["invented"] == ["ZZQ-999"]
    assert bad["M1"]["missing"] == 1
    assert run("a", answer=GOOD, trace={"tool_calls": []})["D3"]["invented"] == NA


def test_d4_d5():
    kr = {**KR, "GIN-90001": {**KR["GIN-90001"], "duration_min": "3"}}
    assert run(answer="3 개월", kr=kr)["D4"]["violations"] == []
    assert run(answer="7 개월", kr=kr)["D4"]["violations"] == ["7"]
    key = {**KEY, "must_rows": ["GCT-90001"]}
    kr2 = {**KR, "GCT-90001": {"gid": "GCT-90001", "target_table": "procedure", "target_code": "ZZP-902",
                               "condition_code": "ZZN-9", "polarity": "금기"}}
    assert run(answer="가짜시술감마", key=key, kr=kr2)["D5"]["G1_missing"] == ["condition:ZZN-9"]
    assert run(answer=GOOD)["D5"]["violations"] == 0


def test_d6_d7_d8_d9():
    assert run(end_state="근거 충족", expected_end_state="근거 충족")["D6"] == {"match": True}
    assert run("c", trace={"tool_calls": []})["D6"] == NA
    assert run()["D7"]["missed_rows"] == 0
    assert run("c", trace={"tool_calls": [{"gids": ["GIN-90001"]}]})["D7"]["missed_rows"] == 1
    assert run("a", trace={"tool_calls": []})["D7"] == NA
    g = synthetic_inputs()["gold"]
    g.names["가짜부작용델타"] = {"adverse:ZZA-901"}
    r = checks.run(column="b", answer=GOOD + " 가짜부작용델타", cited=[], trace=TRACE_B, key=KEY, key_rows=KR, gold=g)
    assert r["D8"]["unasked"]["safety"] == ["adverse:ZZA-901"]
    kr = {"GIN-90001": {**KR["GIN-90001"], "candidate_table": "procedure_case", "candidate_name": "가짜케이스이름"}}
    assert run(answer="가짜케이스이름", kr=kr)["D9"]["case_names_used"] == ["가짜케이스이름"]
    assert run(answer=GOOD)["D9"]["case_names_used"] == []


def test_judge_input_is_blind():
    text = judge.judge_input(question="가짜?", answer="답 [GIN-90001] 과 [fake.items[0]]", key_rows=list(KR.values()),
                             expected_end_state="근거 충족", ledger_check=[], lck_text=[], sample_answer=None,
                             d_results=None, two_readings=False, scope_gap=False)
    ans = text.split("## 채점할 답")[1].split("## 전체 답 키")[0]
    assert "GIN-90001" not in ans and "fake.items" not in ans and "[근거 1]" in ans and "[근거 2]" in ans
    for bad in ("columns", "col_", "\"b\"", "raw_get", "concern_candidates", "end_state\"", "verified"):
        assert bad not in text


def test_judge_validate_requires_failure_evidence():
    s = {k: {"verdict": "pass", "evidence": []} for k in judge.ITEMS}
    s["safety"] = {"verdict": "fail", "evidence": []}
    v = judge.validate({**s, "graded_end_state": "근거 충족", "followed_ledger_error": False})
    assert v["items"]["safety"]["verdict"] == "invalid" and v["items"]["accuracy"]["verdict"] == "pass"


def test_key_rejection_no_unlock():
    good_build = {"gold_hash": "h", "aux_hash": "a"}
    base = {"build": good_build, "confirmed": {"by": "x", "at": "t"}}
    assert accept_key(base, good_build)
    for bad in ({**base, "confirmed": None}, {**base, "synthetic": True},
                {**base, "build": {"gold_hash": "other", "aux_hash": "a"}}, None):
        with pytest.raises(KeyRejected):
            accept_key(bad, good_build)
    for p in (Path(ROOT) / "clinic_chat").rglob("*.py"):
        assert "allow-unconfirmed" not in p.read_text(encoding="utf-8")


async def test_plumbing_without_eval_set(tmp_path, fake_cli, monkeypatch):
    bin_, route, _ = fake_cli
    monkeypatch.delenv("EVAL_SET_DIR", raising=False)
    st = {k: {"verdict": "pass", "evidence": []} for k in judge.ITEMS}
    (route / "judge.jsonl").write_text(stream("", structured={**st, "graded_end_state": "근거 충족",
                                                               "followed_ledger_error": False}), encoding="utf-8")
    s = make_settings(tmp_path, claude_cli_bin=bin_, eval_set_dir=str(tmp_path / "nope"), questions_csv=None,
                      gold_csv_dir=None)
    r = await plumbing(s, tmp_path / "plumb")
    assert r["mark"] == PLUMBING_MARK and r["shape"]["items_5"]
    assert PLUMBING_MARK in (tmp_path / "plumb" / "report.md").read_text(encoding="utf-8")
    assert not (tmp_path / "runs" / "quality.jsonl").exists() and not (tmp_path / "nope").exists()


def test_report_na_not_zero():
    rows, qs = [], []
    for i in range(6):
        rid = f"r{i}"
        rows.append({"run_id": rid, "build": {"gold_hash": "h"}, "models": {"answer": "m"}, "settings": {},
                     "columns": {c: {"timing": {"total_ms": 100 + i}} for c in ("a", "b", "b_on", "c")},
                     "answer_key": {"confirmed": True}})
        cols = {}
        for c in ("a", "b", "b_on", "c"):
            d = run(c, answer=GOOD, trace=TRACE_B if c.startswith("b") else {"tool_calls": []})
            cols[c] = {"checks": d, "judge": {"items": {k: {"verdict": "pass"} for k in judge.ITEMS}}}
        qs.append({"run_id": rid, "columns": cols})
    md = render(rows, qs, title="t")
    d3 = next(ln for ln in md.splitlines() if "D3 지어낸" in ln)
    d7 = next(ln for ln in md.splitlines() if "D7 빠진" in ln)
    assert d3.split("|")[2].strip() == NA and d7.split("|")[2].strip() == NA
    assert "합격선" in md and json.dumps(md)


def test_w5_d4_ignores_citation_indexes_and_list_numbers():
    """W-5 — A 의 원장 인용 배열 번호 · 줄 머리 목록 번호를 숫자로 세지 않는다(세 열 같은 검사기)."""
    a = "1. 가짜시술알파 [x.items[0].note] [x.items[12].x]\n2) 다음 [GIN-90001] [근거 3]"
    assert run(answer=a)["D4"]["violations"] == []
    assert run(answer="1. 7 개월 [x.items[0].note]")["D4"]["violations"] == ["7"]


def test_w6_report_has_all_section10_tables():
    rows, qs = [], []
    for i in range(4):
        rid = f"r{i}"
        rows.append({"run_id": rid, "set_id": f"S{i}", "build": {"gold_hash": "h"}, "models": {"answer": "m"},
                     "settings": {"answer_reasoning": "lowest_effort", "lowest_effort": "low", "effort": "medium"},
                     "columns": {c: {"timing": {"total_ms": 100 + i, "ttft_ms": 10}, "end_state": "근거 충족" if c == "b" else None,
                                     "calls": {"llm_round_trips": 1}, "tokens": {"llm": [{"input_tokens": 5, "output_tokens": 7}]},
                                     "trace": TRACE_B if c == "b" else None} for c in ("a", "b", "b_on", "c")},
                     "answer_key": {"confirmed": True}})
        cols = {}
        for c in ("a", "b", "b_on", "c"):
            d = run(c, answer=GOOD, trace=TRACE_B if c.startswith("b") else {"tool_calls": []})
            cols[c] = {"checks": d, "graded_end_state": "근거 충족",
                       "judge": {"model": "judge-x", "items": {k: {"verdict": "pass"} for k in judge.ITEMS}}}
        qs.append({"run_id": rid, "lck_ids": ["LCK-900"] if i == 0 else [], "columns": cols})
    items = {f"S{i}": {"range": "시술", "expected_end_state": "근거 충족"} for i in range(4)}
    md = render(rows, qs, title="t", items=items)
    for h in ("열별 표 — 전체 (4", "원장 = silver(LCK 가 걸리지 않은 문항) (3", "범위별 표", "상태별 표", "B 전용",
              "추론 끔 / 켬", "judge `judge-x`", "가장 낮은 effort(`low`)", "종료 상태 일치", "ttft_ms", "D9", "M1",
              "빠진 must_rows", "끌어낸 범위에 기대 범위", "읽은 종류 ⊇ 기대 종류", "D10", "답 길이", "K 실패"):
        assert h in md, h
    st = next(ln for ln in md.splitlines() if ln.startswith("| 근거 충족 |"))
    assert st.split("|")[2].strip() == "4"


def test_n2_key_rejected_when_script_or_calls_changed():
    b = {"gold_hash": "h", "aux_hash": "a"}
    k = {"build": b, "confirmed": {"by": "x", "at": "t"}, "script_sha256": "s1", "calls_sha256": "c1"}
    assert accept_key(k, b, script_sha="s1", calls_sha="c1")
    for kw in ({"script_sha": "s2", "calls_sha": "c1"}, {"script_sha": "s1", "calls_sha": "c2"}):
        with pytest.raises(KeyRejected):
            accept_key(k, b, **kw)


def test_n7_d8_counts_adverse_even_if_key_has_condition():
    """N-7 — 키에 condition 만 있으면 답이 늘어놓은 adverse 는 unasked.safety 로 잡힌다(표마다 따로)."""
    import copy

    g = copy.deepcopy(GOLD)
    g.names["가짜사건델타"] = {"adverse:ZZA-9"}
    g.codes.add("adverse:ZZA-9")
    key = {**KEY, "codes": [*KEY["codes"], "condition:ZZN-9"]}
    d = checks.run(column="b", answer=GOOD + " 가짜사건델타", cited=[], trace=TRACE_B, key=key, key_rows=KR, gold=g)
    assert d["D8"]["unasked"]["safety"] == ["adverse:ZZA-9"]


# ───── WORK-006 WP-8 — D1 · D2 해당 없음 · B 의 D2 = 줄 번호 · D10 읽지 않은 것 ─────

def test_d1_d2_na_without_cited_and_b_d2_lines():
    assert run(answer="이름만 말한 답")["D1"] == NA and run(answer="이름만")["D2"] == NA
    tb = {**TRACE_B, "verified_lines": [{"n": 1, "gid": "GIN-90001"}, {"n": 2, "gid": "GIN-90002"}],
          "answer_lines": [{"answer_line": 1, "rows": [1, 2]}, {"answer_line": 2, "rows": [7]}]}
    d = run(answer="답", trace=tb)
    assert d["D2"]["violations"] == [7] and d["D2"]["answer_lines"] == 2 and d["D1"]["violations"] == []


def test_d10_unread_codes_b_c_and_na_for_a():
    d = run(answer=GOOD)
    assert d["D10"] == {"unread_codes": []}
    only_one = run(answer=GOOD, trace={"verified_gids": [{"gid": "GIN-90001"}]})
    assert isinstance(only_one["D10"]["unread_codes"], list)
    assert run("a", answer=GOOD, trace={"tool_calls": []}, raw_exists=lambda p: True)["D10"] == NA
    c = run("c", answer=GOOD, trace={"tool_calls": [{"gids": []}]})
    assert set(c["D10"]["unread_codes"]) == set(c["D3"]["extra_codes"]) | (set(KEY["codes"]) - set(c["D3"]["missing"]))


def test_w3_expected_entries_from_spec_counts_target_match(tmp_path):
    """코드 검수 v05 W-3 — 37 의 빈 `expected_entries` 를 명세 entries 로 채움(결정론) · 보고서 「대상 ⊇ 기대 입구」 가 선다."""
    import json

    from clinic_chat.evalset.loader import fill_expected_entries, load_set, read_set_lines
    from tests.conftest import make_settings

    s = make_settings(tmp_path)
    d = tmp_path / "eval-set"
    (d / "calls").mkdir(parents=True)
    lines = [{"id": "Q98", "source": "37", "expected_entries": []}, {"id": "Q99", "source": "37", "expected_entries": []},
             {"id": "X01", "source": "추가", "expected_entries": []}]
    (d / "set.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    (d / "calls" / "Q98.json").write_text(json.dumps({"entries": [{"table": "procedure", "code": "ZZP-1", "name": "가짜"},
                                                                  {"table": "offer_name", "code": "", "name": "가짜"}]}))
    (d / "calls" / "Q99.json").write_text(json.dumps({"entries": [{"table": "offer_name", "code": "", "name": "가짜"}]}))
    # 채우기 전에도 보고서는 명세 entries 를 읽는다
    assert load_set(s)["Q98"]["spec_entries"] == ["procedure:ZZP-1"]
    r = fill_expected_entries(s)
    assert r["filled"] == 1 and r["empty_spec"] == ["Q99"] and r["still_empty"] == ["Q99", "X01"]
    assert [x["expected_entries"] for x in read_set_lines(s)] == [["procedure:ZZP-1"], [], []]
    assert fill_expected_entries(s)["filled"] == 0  # 두 번 돌려도 같다
    tr = {"front": {"targets": [{"id": "procedure:ZZP-1", "decision": "target", "noul": 0.9}], "kinds": []}}
    rows = [{"run_id": "r0", "set_id": "Q98", "columns": {"b": {"trace": tr, "end_state": "근거 충족"}}}]
    md = render(rows, [], title="t", items=load_set(s))
    assert "대상 ⊇ 기대 입구 | 1/1 · 놓친 0 · 더 둔 0" in md
