"""WORK-008 WP-1 — 세트 칸 U-1 · U-2 · 대상별 기대 종류(`expected_kinds_by_entry` — §11 B-1 코디 결정) · 로더 선택 칸.

모의 허브 · 가짜 코드(ZZ…) · 가짜 이름(「가짜…」)만 쓴다.
"""

from __future__ import annotations

from clinic_chat.evalset import answer_key as ak
from clinic_chat.evalset.key_calls import build_spec, spec_from_item
from clinic_chat.evalset.key_table import kinds_cell
from clinic_chat.evalset.loader import kinds_of_entry, validate
from tests.fake_hub import FakeHub, row

NAMES = {"procedure_case:ZZK-1": "가짜케이스", "modality:ZZM-1": "가짜계열", "procedure:ZZP-1": "가짜시술"}
Q = {"id": "Q90", "range": "시술"}
ALL5 = ["overview", "mechanism", "indication", "outcome", "lineup"]


def _item(**kw) -> dict:
    return {"expected_entries": ["procedure_case:ZZK-1", "modality:ZZM-1"], "expected_kinds": ALL5, **kw}


def _read(spec, node):
    return {k for c in spec["calls"] if c["request"].get("node") == node for k in c["kinds"]}


async def test_by_entry_drops_case_lineup_scope_but_keeps_modality_one():
    spec = await build_spec(Q, FakeHub({}).client(), {}, _item(expected_kinds_by_entry={
        "procedure_case:ZZK-1": ["overview", "mechanism", "indication", "outcome"]}), NAMES)
    assert not [c for c in spec["calls"] if c["tool"] == "scope" and c["request"]["node"] == "procedure_case:ZZK-1"]
    assert [c for c in spec["calls"] if c["tool"] == "scope" and c["request"]["node"] == "modality:ZZM-1"]
    assert _read(spec, "procedure_case:ZZK-1") == {"overview", "mechanism", "indication", "outcome"}
    assert _read(spec, "modality:ZZM-1") == set(ALL5)
    assert spec["expected_kinds"] == ALL5
    assert spec["expected_kinds_by_entry"] == {"procedure_case:ZZK-1": ["overview", "mechanism", "indication", "outcome"]}
    # 판정용(그 범위의 나머지)으로는 케이스의 lineup 이 간다 — 읽기 호출만 좁힌다
    assert [c for c in spec["judge_calls"] if c["tool"] == "scope" and c["request"]["node"] == "procedure_case:ZZK-1"]


async def test_no_by_entry_is_byte_identical_spec():
    a = await build_spec(Q, FakeHub({}).client(), {}, _item(), NAMES)
    b = await build_spec(Q, FakeHub({}).client(), {}, _item(expected_kinds_by_entry={}), NAMES)
    assert a == b and "expected_kinds_by_entry" not in a
    assert {c["request"]["node"] for c in a["calls"] if c["tool"] == "scope"} == {"procedure_case:ZZK-1", "modality:ZZM-1"}


async def test_key_only_entry_follows_of_entry_kinds():
    item = _item(expected_kinds_by_entry={"modality:ZZM-1": ["overview"]},
                 key_lower_entries=[{"node": "procedure:ZZP-1", "of": "modality:ZZM-1"}])
    spec = await build_spec(Q, FakeHub({}).client(), {}, item, NAMES)
    assert _read(spec, "procedure:ZZP-1") == {"overview"} and _read(spec, "modality:ZZM-1") == {"overview"}
    assert _read(spec, "procedure_case:ZZK-1") == set(ALL5)


async def test_kinds_met_has_no_case_lineup_line():
    by = {"procedure_case:ZZK-1": ["overview", "mechanism", "indication", "outcome"]}
    spec = await build_spec(Q, FakeHub({}).client(), {}, _item(expected_kinds_by_entry=by), NAMES)
    hub = FakeHub({
        "facets": {"rows": [row("g_profile", "GPF-90001", subject_table="procedure_case", subject_code="ZZK-1",
                                overview="가짜 개요", mechanism="가짜 원리", layer={"role": "자기"})], "ledger_check": []},
        "scope": {"holders": [row("g_scope", "GSC-90002", subject_table="modality", subject_code="ZZM-1",
                                  holder_table="procedure", holder_code="ZZP-1", role="하위 시술")]}})
    st: dict = {}
    rows, recs, lc, build_, raw = await ak.read_rows(hub.client(), spec["calls"], "procedure", st)
    met = ak.kinds_met(spec, st["per_call"])
    assert not [m for m in met if m["target"] == "procedure_case:ZZK-1" and m["kind"] == "lineup"]
    assert [m for m in met if m["target"] == "modality:ZZM-1" and m["kind"] == "lineup"]
    k = ak.build_key("Q90", spec, rows, recs, lc, build_, set(), {"status": "답함"}, {}, raw, "sha", met=met,
                     lineup=st["lineup"])
    assert k["expected_kinds_by_entry"] == by
    assert "하위 목록(가짜계열 하위)" in kinds_cell(spec, k)  # 표에는 계열 쪽 lineup 줄만 남는다


def test_spec_from_item_uses_by_entry():
    item = {"id": "N90", "range": "시술", "source": "추가", **_item(expected_kinds_by_entry={
        "procedure_case:ZZK-1": ["overview"]})}
    spec = spec_from_item(item, NAMES)
    assert _read(spec, "procedure_case:ZZK-1") == {"overview"} and "lineup" in _read(spec, "modality:ZZM-1")


def test_kinds_of_entry_fallbacks():
    obj = {"expected_kinds": ["overview", "lineup"], "expected_kinds_by_entry": {"modality:ZZM-1": ["lineup"]}}
    assert kinds_of_entry(obj, "modality:ZZM-1") == ["lineup"]
    assert kinds_of_entry(obj, "procedure:ZZP-1", "modality:ZZM-1") == ["lineup"]
    assert kinds_of_entry(obj, "procedure_case:ZZK-1") == ["overview", "lineup"]


def test_loader_checks_optional_field_shapes():
    base = {"id": "Q90", "expected_entries": ["procedure_case:ZZK-1"], "expected_kinds": ["overview"],
            "status": "confirmed"}
    ok = {**base, "key_fix1": {"at": "x", "why": "y"}, "expected_kinds_by_entry": {"procedure_case:ZZK-1": ["overview"]},
          "key_lower_entries": [{"node": "procedure:ZZP-1", "of": "procedure_case:ZZK-1"}]}
    bad = {**base, "key_fix1": {"at": "x"}, "expected_kinds_by_entry": {"modality:ZZM-1": ["nope"]},
           "key_upper_entries": [{"node": "ZZC-1", "of": "concern:ZZC-0"}]}
    shape = lambda errs: [e for e in errs if e.startswith("Q90:") and "칸 없음" not in e]  # noqa: E731
    assert shape(validate([ok])) == []
    got = shape(validate([bad]))
    assert any("key_fix1" in e for e in got) and any("expected_entries 밖" in e for e in got)
    assert any("종류 꼴" in e for e in got) and any("key_upper_entries 꼴" in e for e in got)
