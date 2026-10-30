"""Phase 5 — 품질 ① 단어 검사 · ② Opus 채점 · 채점용 로컬 원장 도구 3."""

from __future__ import annotations

import json

import pytest

from clinic_chat.quality import judge, word_check
from clinic_chat.quality.normalize import make_normalizer
from clinic_chat.quality.raw_tools import RawToolError
from tests.fake_anthropic import FakeAnthropic, quality_policy
from tests.fake_data import SEMANTIC_FIXTURE

NORM = make_normalizer(SEMANTIC_FIXTURE["dictionary"]["resolve_rules"])


@pytest.mark.parametrize("src,out", [
    ("Nd:YAG", "nd:yag"),
    ("듀얼파장(알렉+다이오드 or 다이오드+Nd:YAG)", "듀얼파장알렉+다이오드or다이오드+nd:yag"),  # 한글로 시작 + 영문 대문자 → 소문자
    ("필러(HA/CaHA)", "필러ha/caha"),
    ("쳐짐", "처짐"),  # fold
    ("코그(Cog)", "코그cog"), ("코그 Cog", "코그cog"),  # 괄호 지움 · 안 글자 남김
    ("[울쎄라]", "울쎄라"),
    ("Café", "cafe"),  # 라틴 악센트 접기
    ("ＨＩＦＵ", "hifu"),  # NFKC
])
def test_normalize_table(src, out):
    assert NORM(src) == out


def test_word_match_raw_dict_missing(deps_factory):
    deps = deps_factory()
    names = [{"text": t, "kind": "시술"} for t in
             ["Nd:YAG", "CaHA", "쳐짐", "톡신(보톡스)", "울트라포커스 X", "울세라", "턱", "HIFU 리프팅"]]
    r = word_check.match_names(deps, names)
    by = {x["name"]: x for x in r["names"]}
    assert by["Nd:YAG"]["found"] and by["Nd:YAG"]["via"] == "원장"
    assert by["Nd:YAG"]["where"][0] == {"file": "taxonomy", "path": "taxonomy.modality[1].devices[0]"}
    assert by["CaHA"]["found"] and by["CaHA"]["where"][0]["path"] == "taxonomy.modality[2].name"
    assert by["쳐짐"]["found"] and by["쳐짐"]["norm"] == "처짐"
    assert by["톡신(보톡스)"]["found"]
    assert by["울트라포커스 X"]["found"] is False and by["울트라포커스 X"]["via"] is None
    assert by["울세라"]["via"] == "사전" and by["울세라"]["dict_expr"] == "울쎄라"  # 사전에만 있는 표기
    assert by["HIFU 리프팅"]["via"] == "원장"
    assert r["skipped"] == ["턱"]  # 2자 미만 제외
    assert r["not_found"] == 1
    assert all(len(x["where"]) <= 3 for x in r["names"])


async def test_extract_input_is_answer_only(deps_factory):
    fa = FakeAnthropic(quality_policy)
    deps = deps_factory(anthropic=fa)
    r = await word_check.check_answer(deps, "소프웨이브와 울트라포커스 X 를 권합니다.")
    p = fa.calls[0]
    assert p["model"] == "claude-opus-5-5" and p["output_config"]["effort"] == "low"
    assert p["output_config"]["format"]["type"] == "json_schema"
    assert p["messages"] == [{"role": "user", "content": "소프웨이브와 울트라포커스 X 를 권합니다."}]  # 사전 · 원장 안 줌
    assert "tools" not in p
    assert [x["name"] for x in r["names"]] == ["소프웨이브", "울트라포커스 X"] and r["not_found"] == 1
    assert r["extracted"]


ANSWERS = {"case1": "답 하나 fixture", "case2": "답 둘 fixture", "case3": "답 셋 fixture"}


async def test_judge_blind_evidence_and_no_hub(deps_factory, hub):
    fa = FakeAnthropic(quality_policy)
    deps = deps_factory(anthropic=fa)
    order = judge.blind_order(list(ANSWERS), 11)
    r = await judge.judge(deps, "소프웨이브 사우나?", ANSWERS, order)
    first = fa.calls[0]["messages"][0]["content"]
    for bad in ("case1", "case2", "case3", "raw_search", "resolve", "ms", "ttft"):
        assert bad not in first  # 가리기
    assert all(f"답 {lab}" in first for lab in "ABC")
    for p in fa.calls:
        assert p["model"] == "claude-opus-5-5" and p["output_config"]["effort"] == "medium" and "thinking" not in p
        assert p["tool_choice"]["type"] in ("auto", "none")
        assert [t["name"] for t in p["tools"]] == ["raw_list", "raw_get", "raw_search"]
    assert fa.calls[-1]["output_config"]["format"]["type"] == "json_schema"
    assert r["tool_calls"] == 1
    a_case, b_case = order[0], order[1]
    fa_ = r["per_case"][a_case]["failures"][0]
    assert fa_["item"] == "accuracy" and fa_["status"] == "valid"  # 맞는 경로 · 있는 quote
    fb = r["per_case"][b_case]["failures"][0]
    assert fb["status"] == "invalid"  # 없는 quote
    assert r["per_case"][order[2]]["pass_count"] == 5
    assert hub.calls == []  # 채점 경로 허브 호출 0


def test_evidence_check(deps_factory):
    st = deps_factory().raw_store
    ok = {"file": "procedure-profiles", "path": "procedure-profiles.M9.aftercare", "quote": "당일 사우나"}
    assert judge.check_evidence(st, ok)
    assert judge.check_evidence(st, {**ok, "path": "M9.aftercare"})  # file + 상대 경로
    assert not judge.check_evidence(st, {**ok, "quote": "없는 말"})
    assert not judge.check_evidence(st, {**ok, "path": "procedure-profiles.M7"})
    assert not judge.check_evidence(st, None)


def test_blind_order_seed():
    cs = ["case1", "case2", "case3"]
    assert judge.blind_order(cs, 5) == judge.blind_order(cs, 5)
    assert len({tuple(judge.blind_order(cs, s)) for s in range(20)}) > 1


# 로컬 원장 도구 3 — SPEC-005 §5-12 계약 꼴
def test_raw_list(deps_factory):
    st = deps_factory().raw_store
    r = st.raw_list()
    assert r["raw"] == {"dir_manifest_sha16": st.dir_manifest_sha16, "files": 21}
    assert len(r["files"]) == 21 and set(r["files"][0]) == {"name", "bytes", "sha16", "structure"}
    t = st.raw_list(file="taxonomy", depth=2)["files"][0]["structure"]
    assert t["type"] == "object" and t["children"]["modality"]["type"] == "array"
    assert t["children"]["modality"]["length"] == 3 and t["children"]["modality"]["item"]["type"] == "object"
    assert "value" not in json.dumps(t)  # 값 본문 없음
    with pytest.raises(RawToolError):
        st.raw_list(depth=4)


def test_raw_get_paths_and_truncation(deps_factory):
    st = deps_factory().raw_store
    r = st.raw_get("taxonomy.problem[3]")
    assert set(r) >= {"path", "type", "value", "bytes_total", "truncated", "raw", "request"}
    assert r["value"] == {"id": "P5", "name": "fixture 문제 5"} and r["truncated"] is False
    assert st.raw_get('taxonomy["modality"][0].id')["value"] == "M2"
    s = st.raw_get("extracted.M9.long", max_bytes=100)
    assert s["truncated"] is True and s["type"] == "string" and len(s["value"].encode()) <= 100
    a = st.raw_get("taxonomy.problem", max_bytes=80)
    assert a["truncated"] is True and a["next_offset"] >= 1
    b = st.raw_get("taxonomy.problem", offset=a["next_offset"], max_bytes=20000)
    assert len(a["value"]) + len(b["value"]) == 4 and b["truncated"] is False
    for bad in ("taxonomy.nope", "nofile.x", "taxonomy.problem[9]", "taxonomy..x"):
        with pytest.raises(RawToolError):
            st.raw_get(bad)


def test_raw_search_norm(deps_factory):
    st = deps_factory().raw_store
    a = st.raw_search("hifu  리프팅")
    b = st.raw_search("HIFU리프팅")
    assert a["hits"] == b["hits"] and a["total"] >= 1 and a["query_norm"] == "hifu리프팅"
    assert a["hits"][0] == {"file": "taxonomy", "path": "taxonomy.modality[1].name", "in": "value",
                            "snippet": "HIFU 리프팅"}
    assert st.raw_search("쳐짐")["total"] == 0 and st.raw_search("처짐")["total"] >= 1  # fold 안 씀
    k = st.raw_search("aftercare", limit=1)
    assert k["hits"][0]["in"] == "key" and k["truncated"] is True and k["total"] == 2
    assert [h["file"] for h in st.raw_search("fixture", limit=100)["hits"]] == \
        sorted(h["file"] for h in st.raw_search("fixture", limit=100)["hits"])  # 파일 이름 순
    with pytest.raises(RawToolError):
        st.raw_search("")


async def test_local_raw_tools_same_as_raw_mcp(deps_factory, hub):
    deps = deps_factory()
    async with deps.raw_mcp.session() as s:
        for name, args in [("raw_list", {"file": "taxonomy"}), ("raw_get", {"path": "taxonomy.problem[3]"}),
                           ("raw_search", {"query": "처짐"})]:
            remote = (await s.call(name, args)).data
            local = deps.raw_store.call(name, args)
            assert remote == local


async def test_quality_usage_and_cost_recorded(deps_factory):
    fa = FakeAnthropic(quality_policy)
    deps = deps_factory(anthropic=fa)
    wc = await word_check.check_answer(deps, "소프웨이브 권합니다")
    assert wc["usage"]["input_tokens"] == 100 and wc["cost_usd"] > 0
    jr = await judge.judge(deps, "q", ANSWERS, judge.blind_order(list(ANSWERS), 1))
    assert jr["calls"] == len(fa.calls) - 1 and jr["usage"]["input_tokens"] == 100 * jr["calls"]
    assert jr["cost_usd"] > 0
    for p in fa.calls[1:]:
        assert p["system"][-1]["cache_control"] == {"type": "ephemeral"} and p["cache_control"] == {"type": "ephemeral"}
