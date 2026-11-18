"""WP-6 — 호출 명세(ast · 실행 없음) · 답 키(결정론 · 값 칸 없음 · 기대 종료 상태 다섯 길) · 세트 검증."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clinic_chat.config import ROOT
from clinic_chat.evalset import answer_key as ak
from clinic_chat.evalset.key_calls import LiteralError, read_questions
from clinic_chat.evalset.loader import validate
from tests.fake_hub import FakeHub, row

GB = '''
import os
os.remove("SHOULD_NOT_RUN")
QUESTIONS = [
    dict(id='Q90', range='시술', q='가짜 질문 문장', type='profile', entry='가짜시술', need='x'),
    dict(id='Q91', range='부작용', q='가짜 질문 둘', type='adverse', entry='가짜시술', adverse='가짜사건', need_time=True),
]
'''


def test_reads_questions_by_ast_without_running(tmp_path):
    p = tmp_path / "gold-build.py"
    p.write_text(GB, encoding="utf-8")
    qs = read_questions(p)
    assert [q["id"] for q in qs] == ["Q90", "Q91"] and all("q" not in q for q in qs)
    p.write_text("QUESTIONS = [dict(id=f())]", encoding="utf-8")
    with pytest.raises(LiteralError):
        read_questions(p)


def test_key_calls_has_no_exec_path():
    src = (Path(ROOT) / "clinic_chat/evalset/key_calls.py").read_text(encoding="utf-8")
    for bad in ("exec(", "runpy", "subprocess", "import_module", "__import__"):
        assert bad not in src


def hub_with(rows, lc=None):
    return FakeHub({"facets": {"rows": rows, "ledger_check": lc or []},
                    "resolve": {"candidates": [{"gold_table": "g_entry", "gid": "GEN-90001", "entity_table": "procedure",
                                                "entity_code": "ZZP-1", "text": "가짜시술"}]}})


ROWS = [row("g_adverse", "GAD-90001", target_table="procedure", target_code="ZZP-1", adverse_code="ZZA-1",
            onset_text="", duration_text="", polarity="있음", layer={"role": "자기"}),
        row("g_contraindication", "GCT-90001", target_table="procedure", target_code="ZZP-1", condition_code="ZZN-1",
            polarity="금기")]
CALLS = [{"tool": "facets", "request": {"node": "procedure:ZZP-1", "facet": "adverse"}}]


async def make(hub, *, item=None, qrow=None, asked=None):
    h = hub.client()
    rows, recs, lc, build, raw = await ak.read_rows(h, CALLS)
    return ak.build_key("Q90", {"asked_fields": asked or []}, rows, recs, lc, build, {"procedure:ZZP-1"},
                        qrow or {"status": "답함", "gold_pointers": "GAD-90001"}, item or {}, raw, "callsha")


async def test_deterministic_and_no_value_columns():
    k1, k2 = await make(hub_with(ROWS)), await make(hub_with(ROWS))
    assert ak.dump(k1) == ak.dump(k2)
    assert all(set(r) == set(ak.ROW_KEYS) for r in k1["rows"])
    assert "onset_text" not in ak.dump(k1) and k1["codes"] == ["procedure:ZZP-1"]
    assert "adverse:ZZA-1" in k1["unnamed_codes"] and k1["must_rows"] == ["GCT-90001"]
    assert k1["sample_check"] == {"pointers": 1, "in_key": 1, "missing": [], "out_of_range": [], "range_excluded": {}}


async def test_no_scope_or_entry_rows():
    k = await make(hub_with([*ROWS, row("g_scope", "GSC-90001"), row("g_entry", "GEN-90002")]))
    assert {r["gold_table"] for r in k["rows"]} == {"g_adverse", "g_contraindication"}


async def test_expected_state_five_paths():
    lc = [{"code": "ZZP-1", "lck_ids": ["LCK-9"], "on": "request"}]
    assert (await make(hub_with([], lc)))["expected_end_state_by"] == "(가)"
    unr = [row("g_offer", "GOF-90001", offer_id="ZZO-1", reach="못 닿음")]
    k = await make(hub_with(unr, [{"code": "ZZO-1", "lck_ids": ["LCK-8"], "on": "rows"}]))
    assert k["expected_end_state_by"] == "(나)"
    k = await make(hub_with(ROWS), asked=["g_adverse.onset_text", "g_adverse.duration_text"])
    assert k["expected_end_state_by"] == "(다)"
    assert (await make(hub_with(ROWS)))["expected_end_state_by"] == "status"
    k = await make(hub_with(ROWS), item={"source": "추가", "expected_end_state": "후보 소진"})
    assert (k["expected_end_state"], k["expected_end_state_by"]) == ("후보 소진", "set")


def test_set_validate():
    base = {f: None for f in ("id", "bundle", "range", "question", "expected_end_state", "expected_entries",
                              "expected_kinds", "expected_scope_gap", "answer_key", "ledger_diff", "source",
                              "model_answer", "two_readings", "status")}
    lines = []
    for b, n in (("37", 37), ("추가", 19), ("범위 밖", 4), ("속성 시작", 4), ("이름 없음", 5), ("길 찾기", 16)):
        for i in range(n):
            lines.append({**base, "id": f"{b}{i}", "source": b, "status": "confirmed" if b == "37" else "draft"})
    assert validate(lines) == []
    lines[0]["question"] = "복사된 문장"
    assert validate(lines)
    assert validate(lines[:-1])
    lines[0]["question"] = None
    lines[1]["expected_kinds"] = ["not_a_kind"]
    assert any("expected_kinds" in e for e in validate(lines))


def test_key_files_are_ignored():
    import subprocess

    out = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "data/eval-set/answer-key/Q01.json"],
                         capture_output=True, text=True)
    assert out.stdout.strip()
    assert json.dumps({"x": 1})


async def test_q10_key_ga_ignores_upper_hier_rows():
    lc = [{"code": "ZZC-1", "lck_ids": ["LCK-9"], "on": "request"}]
    up = [row("g_package", "GPK-90001", package_id="ZZG-1", concern_code="ZZC-0",
              hier={"asked_code": "ZZC-1", "match": "상위"})]
    k = await make(hub_with(up, lc))
    assert (k["expected_end_state"], k["expected_end_state_by"]) == ("원천 미확정", "(가)")
    assert [r["gid"] for r in k["rows"]] == ["GPK-90001"]  # 행은 키에 그대로(폭 유지)


async def test_q25_sequence_does_not_read_avoid_class_and_sample_expectation_matches():
    """Q25(코디 결정) — 순서·간격 고정 갈래는 좁게 그대로 · 회피 갈래 표본은 「범위 갈래 밖(기대)」 으로 가른다."""
    from clinic_chat.evalset.key_calls import build_spec

    def pair(p):
        if p.get("classes") in ("회피", ["회피"]):  # 모의 허브는 반복 키를 마지막 값 하나로 받는다
            return {"rows": [row("g_pair", "GPR-90009", from_table="procedure", from_code="ZZP-1", to_table="procedure",
                                 to_code="ZZP-2", question_class="회피")], "ledger_check": []}
        return {"rows": [row("g_pair", "GPR-90001", from_table="procedure", from_code="ZZP-1", to_table="procedure",
                             to_code="ZZP-2", question_class="간격")], "ledger_check": []}

    def resolve(p):
        code = "ZZP-1" if p.get("text") == "가짜시술" else "ZZP-2"
        return {"candidates": [{"gold_table": "g_entry", "gid": "GEN-90001", "entity_table": "procedure",
                                "entity_code": code, "text": p.get("text"), "matched": {"span": p.get("text")}}]}

    hub = FakeHub({"pair": pair, "resolve": resolve})
    spec = await build_spec({"id": "Q90", "range": "순서·간격", "entry": "가짜시술", "other": "가짜시술둘",
                             "classes": ["간격", "회피"]}, hub.client(), {"gold_tables": "g_pair"})
    assert spec["expected_kinds"] == ["order_interval"]
    assert spec["range_excluded"] == {"classes": ["회피"]}
    assert all(c["request"]["classes"] == ["순서", "간격", "재시술 간격"] for c in spec["calls"])  # 회피를 읽지 않는다
    h = hub.client()
    rows, recs, lc, build, raw = await ak.read_rows(h, spec["calls"], spec["range_code"])
    k = ak.build_key("Q90", spec, rows, recs, lc, build, {"procedure:ZZP-1"},
                     {"status": "답함", "gold_pointers": "GPR-90001 · GPR-90009"}, {}, raw, "sha",
                     await ak.excluded_gids(h, spec))
    assert [r["gid"] for r in k["rows"]] == ["GPR-90001"]
    assert k["sample_check"]["missing"] == [] and k["sample_check"]["out_of_range"] == ["GPR-90009"]
    flags = ak.summary_row(k, spec, {"status": "답함"})["flags"]
    assert "표본이 키에 안 듦" not in flags and any("BR3" in f for f in flags)


def test_w8_confirm_set_items_and_spec_from_set(tmp_path):
    from clinic_chat.evalset.key_calls import spec_from_item
    from clinic_chat.evalset.loader import confirm_items, read_set_lines, write_set_lines
    from tests.conftest import make_settings

    s = make_settings(tmp_path)
    (tmp_path / "eval-set").mkdir()
    write_set_lines(s, [{"id": "N90", "source": "추가", "status": "draft", "range": "시술"},
                        {"id": "N91", "source": "범위 밖", "status": "draft", "range": "범위 밖"}])
    r = confirm_items(s, ["N90", "N99"], "tester")
    assert r == {"confirmed": ["N90"], "unknown": ["N99"], "already": []}
    lines = read_set_lines(s)
    assert lines[0]["status"] == "confirmed" and lines[0]["confirmed"]["by"] == "tester" and lines[1]["status"] == "draft"
    spec = spec_from_item({"id": "N90", "range": "시술", "entries": ["procedure:ZZP-1"],
                           "expected_kinds": ["overview", "outcome"]}, {"procedure:ZZP-1": "가짜시술"})
    assert spec["range_code"] == "procedure" and [c["request"]["facet"] for c in spec["calls"]] == [
        "profile", "outcome"]
    empty = spec_from_item({"id": "N91", "range": "범위 밖", "expected_entries": []}, {})
    assert empty["calls"] == [] and empty["range_code"] == "out_of_scope"
