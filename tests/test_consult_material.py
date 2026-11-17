"""WP-5 — 재료 덧붙이기(T14 · T15 · T7 꼬리 · T26) · 모의 허브 · 모의 Jev."""

from __future__ import annotations

import hashlib
import json
import subprocess
from types import SimpleNamespace

from clinic_chat.columns.base import Deps
from clinic_chat.columns.col_b import TAIL_MARK, _TailFilter, split_tail, walk
from clinic_chat.config import ROOT
from clinic_chat.consult import material as mat
from clinic_chat.consult.prompt import load
from clinic_chat.consult.tail import TailFilter
from clinic_chat.consult.tail import split_tail as c_split
from tests.conftest import make_settings
from tests.consult_fixture import consult_hub
from tests.fake_jev import FakeJev
from tests.walk_fixture import jev_noul

PROMPT = load(ROOT / "prompts" / "consult.json")


async def _walk(tmp_path, q, kinds, hub=None):
    s = make_settings(tmp_path)
    d = Deps(s, (hub or consult_hub()).client(), FakeJev(noul_fn=jev_noul(kinds=kinds)).jev(s))
    return await walk(q, d)


def _sent(tm):
    return {x["gid"]: x["n"] for x in tm.new_lines if not x.get("summary")}, \
        {x["key"]: x["n"] for x in tm.new_lines if x.get("summary")}


async def test_t14_second_turn_only_new_rows_and_seen_lines(tmp_path):
    w1 = await _walk(tmp_path, "가짜시술 개요", {"overview": 0.9})
    tm1 = mat.renumber(w1, {}, {}, 1)
    assert [x["n"] for x in tm1.new_lines] == list(range(1, tm1.next_n))
    sent, summ = _sent(tm1)
    w2 = await _walk(tmp_path, "가짜시술 개요 다시", {"overview": 0.9})
    assert w2.gate["ok"]  # 걷기 안 번호는 1 부터 그대로 — gate(K1) 통과
    tm2 = mat.renumber(w2, sent, summ, tm1.next_n)
    assert not tm2.groups and set(tm2.seen_lines) == set(sent.values())  # 다시 읽은 줄은 seen_lines 에만
    msg = mat.message(PROMPT, mat.judgement_of(w2, tm2, 2), tm2, "q2")
    assert "GPF-95001" not in msg and PROMPT.turn_headers["empty"] in msg
    assert json.loads(msg.split("\n")[1])["seen_lines"] == tm2.seen_lines and tm2.next_n == tm1.next_n
    # 새 행이 있으면 번호는 next_n 부터 이어진다
    w3 = await _walk(tmp_path, "가짜시술둘 개요", {"overview": 0.9})
    tm3 = mat.renumber(w3, sent, summ, tm1.next_n)
    assert tm3.new_lines and tm3.new_lines[0]["n"] == tm1.next_n


def test_walk_material_and_gate_untouched():
    """W-1 — `walk/material.py` · `walk/gate.py` 는 HEAD 그대로(sha)."""
    for f in ("clinic_chat/walk/material.py", "clinic_chat/walk/gate.py"):
        head = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{f}"], capture_output=True, check=True).stdout
        assert hashlib.sha256(head).hexdigest() == hashlib.sha256((ROOT / f).read_bytes()).hexdigest(), f


async def test_t15_summary_lines_and_w2_reread_full(tmp_path):
    w = await _walk(tmp_path, "가짜고민 시술", {"indication": 0.9})
    tm = mat.renumber(w, {}, {}, 1)
    msg = mat.message(PROMPT, mat.judgement_of(w, tm, 1), tm, "q")
    assert not tm.groups and len(tm.summary_groups) == 1
    rows = tm.summary_groups[0]["rows"]
    assert [r["modality_name"] for r in rows] == ["가짜계열A", "가짜계열B", "가짜계열C", ""]  # 계열마다 한 줄
    assert '"가짜시술"' not in msg and "가짜시술둘" not in msg  # 시술 이름 줄 없음
    led = [x for x in tm.new_lines if x.get("summary")]
    assert led[0]["gids"] == ["GIN-95001", "GPF-95001", "GIN-95003", "GPF-95003"]  # 근거 되짚기
    sent, summ = _sent(tm)
    assert sent == {}  # W-2 — 요약의 gid 는 「실은 gid」 가 아니다
    w2 = await _walk(tmp_path, "가짜시술 개요", {"overview": 0.9})
    tm2 = mat.renumber(w2, sent, summ, tm.next_n)
    assert any(x.get("gid") == "GPF-95001" for x in tm2.new_lines)  # 요약에 든 행을 칸째 읽으면 새 줄
    # B-6 — 같은 요약은 다시 안 싣고 그 번호를 seen_lines 로
    w3 = await _walk(tmp_path, "가짜고민 시술 또", {"indication": 0.9})
    tm3 = mat.renumber(w3, sent, summ, tm.next_n)
    assert not tm3.summary_groups and set(tm3.seen_lines) == {x["n"] for x in led}


def test_conflict_lines_moved_to_conversation_numbers():
    groups = [{"group": 1, "table": "g_pair", "same": {}, "marks": {}, "lines": [1, 2, 3],
               "rows": [{"n": 1, "marks": {"conflict_lines": [3]}}, {"n": 2}, {"n": 3, "marks": {"conflict_lines": [1]}}]}]
    lines = [{"n": i, "gid": f"GPR-0000{i}", "gold_table": "g_pair"} for i in (1, 2, 3)]
    w = SimpleNamespace(material=SimpleNamespace(groups=groups, lines=lines), reads=[])
    tm = mat.renumber(w, {"GPR-00001": 7}, {}, 20)
    rows = tm.groups[0]["rows"]
    assert [r["n"] for r in rows] == [20, 21] and tm.seen_lines == [7]
    assert rows[1]["marks"]["conflict_lines"] == [7]  # 짝이 앞 턴 줄이면 그 옛 번호
    assert tm.groups[0]["lines"] == [20, 21]


def test_ap8_too_large():
    assert mat.too_large(PROMPT, "x" * 3000, 100) and not mat.too_large(PROMPT, "x", 10_000)


def _feed(f, parts):
    out = []
    for p in parts:
        r = f({"event": "delta", "text": p})
        if r:
            out.append(r["text"])
    return "".join(out)


def test_t7_tail_split_over_two_deltas_and_t26_same_as_col_b():
    assert PROMPT.tail_marker == TAIL_MARK
    text = "답 첫 줄\n둘째 줄\n" + TAIL_MARK + "\n1: 3,4\n2: 5-6\n"
    for cut in range(1, len(text)):
        parts = [text[:cut], text[cut:]]
        a, b = _feed(TailFilter(TAIL_MARK), parts), _feed(_TailFilter(), parts)
        assert a == b and "1: 3" not in a and TAIL_MARK not in a
    for t in (text, "꼬리 없음", "본문\n" + TAIL_MARK + "\n깨진 꼬리"):
        assert c_split(t, TAIL_MARK) == split_tail(t)
    body, lines, _ = c_split(text, TAIL_MARK)
    assert lines == [{"answer_line": 1, "rows": [3, 4]}, {"answer_line": 2, "rows": [5, 6]}]
