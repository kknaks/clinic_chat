"""S8-T1 — 목록 글(SPEC-008 LD1 ~ LD8 · WORK-017 WP-1). 가짜 허브(쪽 둘 이상) · 외부 호출 0."""

from __future__ import annotations

import asyncio

import pytest

from clinic_chat.consult import entry_list as el_mod
from tests.fake_hub import FakeHub
from tests.v04_fixture import list_route


@pytest.fixture(autouse=True)
def _clear():
    el_mod.clear_cache()
    yield
    el_mod.clear_cache()


def _hub(rows=None, *, gold_hash="fakehash", fail_on=None):
    hub = FakeHub({"resolve": list_route(rows)}, limit_cap=3, fail_on=fail_on)
    orig = hub.handle

    def handle(request):
        import json

        import httpx2
        resp = orig(request)
        body = json.loads(resp.content)
        if isinstance(body, dict) and "build" in body:
            body["build"] = {**body["build"], "gold_hash": gold_hash}
        return httpx2.Response(resp.status_code, json=body)
    hub.handle = handle  # type: ignore[method-assign]
    return hub


def _load(hub, **kw):
    return asyncio.run(el_mod.load(hub.client(page_limit=3), **kw))


def test_s8_t1_heads_lines_aliases_and_pages():
    hub = _hub()
    el = _load(hub, aliases=True)
    lines = el.text.splitlines()
    assert lines[0] == "## 고민 (table = concern)"
    heads = [x for x in lines if x.startswith("## ")]
    assert heads == ["## 고민 (table = concern)", "## 부위 (table = site)", "## 계열 (table = modality)",
                     "## 시술 (table = procedure)", "## 제품 (table = product)", "## 효과 (table = effect)",
                     "## 케이스 (table = procedure_case)"]  # 0.5 N1 — 효과 · 케이스 더함
    assert "ZZC-1 가짜고민 (= 가짜고민별칭)" in lines  # 같은 글자 별칭 한 번
    assert "ZZC-2 가짜윗고민" in lines  # 별칭 없으면 괄호 없음
    assert lines.index("ZZC-2 가짜윗고민") < lines.index("ZZC-9 가짜팔자")  # 허브 순서
    assert sum(1 for t, _ in hub.requests if t == "resolve") > 1  # 쪽 넘김 끝까지
    assert all("text" not in p for t, p in hub.requests if t == "resolve")  # 목록 꼴(text 없음)
    assert all(p.get("tables") == "concern,site,modality,procedure,product,effect,procedure_case"
               for t, p in hub.requests if t == "resolve")
    assert len(el.items) == 15 and el.counts()["concern"] == 5
    assert ("adverse", "ZZA-1") not in el.items and "ZZA-1" not in el.text  # LD8 — 다섯 표 밖 0
    assert el.items[("concern", "ZZC-1")].aliases == ("가짜고민별칭",)


def test_s8_t1_aliases_off_drops_parens_only():
    on = _load(_hub(), aliases=True, cache=False)
    off = _load(_hub(), aliases=False, cache=False)
    assert "(=" not in off.text
    assert off.text == "\n".join(x.split(" (= ")[0] for x in on.text.splitlines())


def test_s8_t1_same_input_same_sha_and_cache_by_gold_hash():
    hub = _hub()
    a = _load(hub, aliases=True)
    n = len(hub.requests)
    b = _load(hub, aliases=True, gold_hash="fakehash")  # 캐시 — 허브 0
    assert b is a and len(hub.requests) == n
    c = _load(_hub(), aliases=True, cache=False)
    assert c.sha == a.sha and c.text == a.text  # 결정론
    hub2 = _hub(gold_hash="otherhash")
    d = _load(hub2, aliases=True, gold_hash="otherhash")  # 대화 판이 다르면 다시 받는다
    assert len(hub2.requests) > 0 and d.gold_hash == "otherhash"


def test_s8_t1_incomplete_paging_is_error():
    with pytest.raises(el_mod.EntryListError):
        _load(_hub(fail_on={"resolve"}), aliases=True)


def test_l3_first_turn_checks_current_gold_hash():
    """검수 L-3 — 첫 턴(대화 판 없음)은 목록 첫 쪽 머리로 지금 판을 본다: 같은 판이면 캐시 · 바뀌었으면 다시 받는다."""
    a = _load(_hub(gold_hash="h1"), aliases=True)
    hub = _hub(gold_hash="h1")
    b = _load(hub, aliases=True)
    assert b is a and [p.get("limit") for t, p in hub.requests if t == "resolve"] == ["1"]
    hub2 = _hub(gold_hash="h2")
    c = _load(hub2, aliases=True)
    assert c.gold_hash == "h2" and c is not a
