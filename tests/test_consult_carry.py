"""WP-9 — 이어 묻기(T12 · T13 · CR1 ~ CR6 · 검수 W-1 · N-5) · 동결 파일(T18)."""

from __future__ import annotations

import hashlib
import subprocess

from clinic_chat import prompts
from clinic_chat.config import ROOT
from clinic_chat.consult.carry import candidates
from clinic_chat.consult.prompt import load
from clinic_chat.walk import end_state as es
from clinic_chat.walk.front import front
from tests.conftest import make_settings
from tests.fake_jev import FakeJev
from tests.walk_fixture import C1, P1, cand, jev_noul, make_hub

JF = load(ROOT / "prompts" / "consult.json").jev_front
CARRY = [{"table": "concern", "code": "ZZC-1", "name": "가짜고민", "from_turn": 1},
         {"table": "procedure", "code": "ZZP-9", "name": "가짜시술구", "from_turn": 1}]


async def run(tmp_path, hub, noul, **kw):
    s = make_settings(tmp_path)
    fj = FakeJev(noul_fn=noul)
    return await front("그중 그거", hub.client(), fj.jev(s), s, **kw), fj


def dec(f):
    return {t["id"]: t["decision"] for t in f.trace_front["targets"]}


async def test_t12_carried_yes_both_targets_no_overlap_one_request(tmp_path):
    f, fj = await run(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(target=0.9), carry=CARRY, instr=JF)
    assert dec(f) == {"procedure:ZZP-1": "target", "concern:ZZC-1": "carried", "procedure:ZZP-9": "carried"}
    assert [e.node for e in f.entries] == ["procedure:ZZP-1", "concern:ZZC-1", "procedure:ZZP-9"]
    car = [e for e in f.entries if e.by == "carried"]
    assert all(e.read_as == {"by": "carry", "from_turn": 1, "table": e.table, "code": e.code} for e in car)
    assert not f.trace_entry["overlaps"]  # W-1 — 겹침 묶음에 안 든다(overlap 0)
    assert len(fj.requests) == 1  # N-5 — 한 묶음(32) 안이면 Jev 요청 수가 안 는다
    st = fj.requests[0]["state"]["candidates"]
    assert st[1]["matched"] is None and st[1]["carried_from"] == 1  # CR3
    instr = fj.requests[0]["questions"]["target_1"]["instructions"]
    assert "carried_from" in instr and "가짜고민" in instr  # target_carried 절
    assert [x["decision"] for x in f.carried] == ["carried", "carried"]


async def test_t12_between_and_no_are_dropped(tmp_path):
    vals = {0: 0.9, 1: 0.5, 2: 0.1}
    f, _ = await run(tmp_path, make_hub(resolve=[cand(P1)]), jev_noul(target=lambda i, st: vals[i]), carry=CARRY)
    assert dec(f)["concern:ZZC-1"] == "dropped" and dec(f)["procedure:ZZP-9"] == "dropped"
    assert [e.node for e in f.entries] == ["procedure:ZZP-1"]
    assert {d["id"] for d in f.trace_front["dropped"]} == {"concern:ZZC-1", "procedure:ZZP-9"}


async def test_t12_same_as_this_turn_not_added_again(tmp_path):
    f, fj = await run(tmp_path, make_hub(resolve=[cand(C1)]), jev_noul(target=0.9), carry=CARRY[:1])
    assert dec(f) == {"concern:ZZC-1": "target"} and len(fj.requests[0]["state"]["candidates"]) == 1


async def test_cr6_only_carried_and_all_dropped_goes_entry_pick(tmp_path):
    f, fj = await run(tmp_path, make_hub(resolve=[]), jev_noul(target=0.9), carry=CARRY[:1])
    assert [e.node for e in f.entries] == ["concern:ZZC-1"] and f.trace_entry_pick is None
    f2, fj2 = await run(tmp_path, make_hub(resolve=[]), jev_noul(target=0.1, entry=0.1), carry=CARRY[:1])
    assert dec(f2) == {"concern:ZZC-1": "dropped"} and not any(e.by == "carried" for e in f2.entries)
    assert any(k.startswith("entry_") for r in fj2.requests for k in r["questions"]) or f2.end  # 지금의 입구 고르기 길


async def test_t13_no_carry_same_as_before(tmp_path):
    hub = make_hub(resolve=[cand(P1)])
    a, fa = await run(tmp_path, hub, jev_noul(target=0.9))
    b, fb = await run(tmp_path, hub, jev_noul(target=0.9), carry=None, instr=None)
    c, fc = await run(tmp_path, hub, jev_noul(target=0.9), instr={k: JF[k] for k in ("target", "kind", "gap")})
    assert a.trace_front == b.trace_front == c.trace_front and a.trace_entry == b.trace_entry
    assert fa.requests == fb.requests == fc.requests
    assert prompts.jev_sections("front")["target"] == JF["target"]


def test_cr1_candidates_skip_error_and_end_states():
    t = [{"turn": 1, "error": None, "end_state": es.OK, "targets": [{"table": "concern", "code": "A", "name": "a"}]},
         {"turn": 2, "error": None, "end_state": es.OUT, "targets": [{"table": "concern", "code": "B", "name": "b"}]},
         {"turn": 3, "error": {"kind": "exit"}, "end_state": es.OK, "targets": [{"table": "x", "code": "C"}]}]
    assert candidates(t, 1) == [{"table": "concern", "code": "A", "name": "a", "from_turn": 1}]
    assert candidates(t, 0) == []
    t.append({"turn": 4, "error": None, "end_state": es.EXHAUSTED, "targets": [{"table": "p", "code": "D", "name": "d"}]})
    assert [x["code"] for x in candidates(t, 2)] == ["D", "A"]


FROZEN = ["prompts/common.md", "prompts/col_a.md", "prompts/col_b.md", "prompts/col_c.md", "prompts/judge.md",
          *[f"prompts/jev/{f}" for f in prompts.JEV_FILES], "clinic_chat/walk/material.py", "clinic_chat/walk/gate.py",
          "clinic_chat/llm/cli.py", "clinic_chat/llm/cli_process.py", "clinic_chat/llm/auth.py"]


def test_t18_frozen_files_and_web_unchanged():
    files = FROZEN + subprocess.run(["git", "-C", str(ROOT), "ls-files", "clinic_chat/web"], capture_output=True,
                                    text=True, check=True).stdout.split()
    for f in files:
        head = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{f}"], capture_output=True, check=True).stdout
        assert hashlib.sha256(head).hexdigest() == hashlib.sha256((ROOT / f).read_bytes()).hexdigest(), f
