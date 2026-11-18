"""S8-T12 · S8-T14 · S8-T15 · S8-T18(켠 턴 Jev · resolve 맞춤 꼴 0) — 한 턴 흐름(SPEC-008 §2 · §4-1 · WORK-017 WP-7)."""

from __future__ import annotations

import pytest

from tests.conftest import read_log
from tests.state_fixture import Run, state_hub
from tests.v04_fixture import out, set_map, set_out

SITES = {"ZZC-1": [{"site_code": "SITE-014", "site_name": "가짜이마", "concern_code": "ZZC-1", "csi_id": "CSI-1",
                    "match": "직접"},
                   {"site_code": "SITE-015", "site_name": "가짜미간", "concern_code": "ZZC-3", "csi_id": "CSI-3",
                    "match": "하위"}]}


def no_jev(name, instr, state):
    raise AssertionError("켠 턴에서 Jev 를 부르면 안 된다")


def understand_calls(log):
    return [x for x in read_log(log) if "--no-session-persistence" in x["argv"]]


def test_s8_t18_on_turn_no_jev_no_resolve_text(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli, noul=no_jev)
    try:
        set_out(fake_cli[1], [("concern", "ZZC-1")], kinds=["indication"])
        row, kinds, _ = r.turn("가짜고민 시술")
        assert row["error"] is None and "done" in kinds
        assert r.fj.requests == []
        assert not [p for t, p in r.hub.requests if t == "resolve" and p.get("text")]
        assert len(understand_calls(r.log)) == 1
    finally:
        r.close()


@pytest.mark.parametrize("case", ["timeout", "cli", "json", "list"])
def test_s8_t15_failures(tmp_path, fake_cli, monkeypatch, case):
    hub = state_hub()
    kw = {}
    if case == "list":
        hub.routes["resolve"] = lambda p: (500, {"detail": "internal: fixture"})
    if case == "timeout":
        monkeypatch.setenv("FAKE_CLI_SLEEP_UNDERSTAND", "1")
        kw["chat_pick_timeout_sec"] = 0.01
    r = Run(tmp_path, fake_cli, hub=hub, noul=no_jev, **kw)
    try:
        if case == "json":
            set_out(fake_cli[1], raw="이해 못함")
        elif case == "cli":
            (fake_cli[1] / "understand.json").write_text("not json", encoding="utf-8")
        else:
            set_out(fake_cli[1], [("concern", "ZZC-1")])
        row, kinds, _ = r.turn("가짜고민 시술")
        u = row["consult"]["understand"]
        assert u["error"]["kind"] == case
        assert row["consult"]["diff"] == [] or all(d["op"] == "asked" for d in row["consult"]["diff"])
        assert row["consult"]["queries"] == [] and "list" not in kinds and "sites" not in kinds
        assert row["end_state"] == "입구 없음" and "done" in kinds and "turn_error" not in kinds
        assert r.fj.requests == [] and not [p for t, p in r.hub.requests if t == "resolve" and p.get("text")]
        n = len(understand_calls(r.log))
        assert n == 0 if case == "list" else (n <= 1 if case == "timeout" else n == 1)  # 재시도 0
    finally:
        r.close()


def test_s8_t15_next_turn_normal_and_failed_mark(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli)
    try:
        set_out(fake_cli[1], raw="깨짐")
        r.turn("가짜고민 시술")
        set_out(fake_cli[1], [("concern", "ZZC-1")], kinds=["indication"])
        row, kinds, _ = r.turn("가짜고민이요")
        assert row["consult"]["understand"]["error"] is None and "list" in kinds
        assert "(이해 실패)" in understand_calls(r.log)[-1]["stdin"]
    finally:
        r.close()


def test_s8_t12_dots_direct_only_and_withdraw(tmp_path, fake_cli):
    set_map(fake_cli[1], {"됐어요": out([], kind="talk", withdraw=["concern:ZZC-1"]),
                          "가짜고민": out([("concern", "ZZC-1")], kinds=["indication"]), "*": out([])})
    r = Run(tmp_path, fake_cli, hub=state_hub(sites=SITES), chat_ask_max=0)
    try:
        row, _, _ = r.turn("가짜고민 시술")
        assert [d["site_code"] for d in row["sites"]["dots"]] == ["SITE-014"]  # 하위(SITE-015)는 점 아님
        row2, _, _ = r.turn("가짜고민은 됐어요")
        assert row2["sites"]["dots"] == []
    finally:
        r.close()


def test_s8_t14_nq5_skipped_on_qy7_new_turn(tmp_path, fake_cli):
    r = Run(tmp_path, fake_cli)
    try:
        set_out(fake_cli[1], [("site", "ZZS-1")], sites=["ZZS-1"])
        row1, _, _ = r.turn("가짜부위요")
        assert row1["consult"]["ask"] is not None
        set_out(fake_cli[1], [("site", "ZZS-1")], sites=["ZZS-1"])
        row2, _, _ = r.turn("가짜부위 다시요")  # 앞 ask 에 답 안 함 + QY7 이 선 new 턴 → 묻는다
        assert row2["consult"]["ask_why"] != "NQ5"
    finally:
        r.close()


def test_s8_t12_own_direct_site_wins_over_earlier_sub_row(tmp_path, fake_cli):
    """DT1 — 앞 고민 응답에 「하위」 로 실린 고민이 이번 턴 고민이 되면 그 고민 자기 응답의 「직접」 부위가 점이 된다."""
    sites = {"ZZC-2": [{"site_code": "SITE-014", "site_name": "가짜이마", "concern_code": "ZZC-3", "csi_id": "CSI-3",
                        "match": "하위"}],
             "ZZC-3": [{"site_code": "SITE-014", "site_name": "가짜이마", "concern_code": "ZZC-3", "csi_id": "CSI-3",
                        "match": "직접"}]}
    set_map(fake_cli[1], {"하위": out([("concern", "ZZC-3")], kinds=["indication"]),
                          "윗": out([("concern", "ZZC-2")], kinds=["indication"]), "*": out([])})
    r = Run(tmp_path, fake_cli, hub=state_hub(sites=sites), chat_ask_max=0)
    try:
        row1, _, _ = r.turn("가짜윗고민")
        assert row1["sites"]["dots"] == []
        row2, _, _ = r.turn("가짜하위하나요")
        assert [d["site_code"] for d in row2["sites"]["dots"]] == ["SITE-014"]
    finally:
        r.close()


def test_c1_on_turn_judgement_has_synergy_lines(tmp_path, fake_cli):
    """검수 C-1 — 켠 `new` 턴 「앱 판정」 에 `synergy_lines` 가 있다(0.3 그대로 · AN1)."""
    import json
    set_map(fake_cli[1], {"가짜고민": out([("concern", "ZZC-1")], kinds=["indication"]),
                          "가짜제품": out([("product", "ZZR-1")], kinds=["overview"]), "*": out([])})
    r = Run(tmp_path, fake_cli)
    try:
        r.turn("가짜제품 받고 싶어요")
        row, _, _ = r.turn("가짜고민 때문에요")
        j = json.loads(row["llm_user_message"].split("\n")[1])
        assert "synergy_lines" in j and j["synergy_lines"] == row["consult"]["synergy_lines"] and j["synergy_lines"]
    finally:
        r.close()


def test_c2_non_target_concern_dots_from_walk_reads(tmp_path, fake_cli):
    """검수 C-2 — 고민 둘 + 부위 첫 턴: 대상이 아닌(focus 아닌) 고민의 직접 부위도 점이다(걷기 읽기를 점 재료로)."""
    sites = {"ZZC-1": [{"site_code": "SITE-014", "site_name": "가짜이마", "concern_code": "ZZC-1", "csi_id": "C1",
                        "match": "직접"}],
             "ZZC-2": [{"site_code": "SITE-015", "site_name": "가짜미간", "concern_code": "ZZC-2", "csi_id": "C2",
                        "match": "직접"}]}
    set_map(fake_cli[1], {"*": out([("concern", "ZZC-1"), ("site", "ZZS-1"), ("concern", "ZZC-2")], sites=["ZZS-1"],
                                   kinds=["indication"])})
    r = Run(tmp_path, fake_cli, hub=state_hub(sites=sites), chat_ask_max=0)
    try:
        row, _, _ = r.turn("가짜고민 가짜부위 가짜윗고민")
        assert row["consult"]["after"]["focus"] == "ZZC-2"
        assert {d["site_code"] for d in row["sites"]["dots"]} == {"SITE-014", "SITE-015"}
    finally:
        r.close()


def test_l5_qy7_new_counts_axis_items_only(tmp_path, fake_cli):
    """검수 L-5 — 부위 + 애매한 고민(guess)도 「대상이 부위뿐」(축 기준) → NQ5 를 걸지 않는다."""
    r = Run(tmp_path, fake_cli)
    try:
        set_out(fake_cli[1], [("site", "ZZS-1")], sites=["ZZS-1"])
        r.turn("가짜부위요")
        set_out(fake_cli[1], [("site", "ZZS-1"), ("concern", "ZZC-2", False)], sites=["ZZS-1"])
        row, _, _ = r.turn("가짜부위에도 있어요")
        assert row["consult"]["ask_why"] != "NQ5"
    finally:
        r.close()


def test_l1_withdraw_only_new_is_talk_and_screen_updates(tmp_path, fake_cli):
    """검수 L-1 — 대상 없이 거둠만 있는 `new` = `talk` 갈래(`from` = withdraw_only) · 객체가 바뀌어 리스트가 다시 간다."""
    r = Run(tmp_path, fake_cli, chat_ask_max=0)
    try:
        set_out(fake_cli[1], [("concern", "ZZC-1"), ("product", "ZZR-1")], kinds=["indication"])
        r.turn("가짜고민 가짜제품")
        set_out(fake_cli[1], [], kind="new", withdraw=["product:ZZR-1"])
        row, kinds, _ = r.turn("가짜제품은 빼 주세요")
        assert row["consult"]["turn_kind"] == {"kind": "talk", "from": "withdraw_only"}
        assert any(d["op"] == "withdraw" for d in row["consult"]["diff"]) and "list" in kinds
    finally:
        r.close()
