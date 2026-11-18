"""0.3 WP-8 — E6 입력 파일 꼴 · 요약 함수만(외부 호출 0 · SPEC-006 §11-3 · WORK-014 §14 · W-12 ③)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from clinic_chat.consult import e6
from tests.conftest import make_settings

ROOT = Path(__file__).resolve().parent.parent
HANGUL = re.compile(r"[가-힣]")


def test_inputs_shape_and_turn_counts():
    cs = e6.inputs()
    assert [c["id"] for c in cs] == ["c1_e3f90", "c2_5c0d6", "c3_80581", "sp_cheek"]
    main = cs[:3]
    assert sum(len(c["turns"]) for c in main) == 12 and sum(len(c["turns"]) - 1 for c in main) == 9  # W-12 ③
    assert all(isinstance(t, str) and t for c in cs for t in c["turns"]) and len(cs[3]["turns"]) == 1


def _row(n, kind, *, by=None, said=None, voice=0, answer="답이에요.", nouls=None):
    return {"turn": n, "question": f"q{n}", "answer": answer, "end_state": None if kind == "talk" else "근거 충족",
            "targets": [], "error": None, "list": None, "sites": None, "material": {"new_lines": []},
            "timing": {"first_list_ms": 1.0, "ttft_ms": 2.0, "total_ms": 3.0}, "llm_user_message": "m",
            "consult": {"turn_kind": {"kind": kind, "nouls": nouls, "rule": "r", "requests": 1, "ms": 5.0, "error": None,
                                      "walks": [{"entry_pick": False, "used": kind != "talk", "ms": 1.0,
                                                 "cancelled": kind == "talk"}]},
                        "close": {"by": by, "said": said, "alt": [], "offered": []},
                        "voice": {"n": voice, "hits": [{"pattern": "p", "text": "t"}] * voice},
                        "queries": [], "after": {"focus": None, "concerns": []}}}


def test_summary_and_aggregate():
    t1 = e6.summary(_row(1, "new", by="ask", said=0), [("list", "x"), ("delta", "a"), ("done", "d")],
                    [{"tool": "resolve"}, {"tool": "resolve"}], True)
    assert t1["hub_requests"] == {"resolve": 2} and t1["events"] == ["list", "delta", "done"] and t1["hub_v07"] is True
    t2 = e6.summary(_row(2, "talk", by=None, answer="그러셨군요?", voice=2), [("done", "d")], [{"tool": "resolve"}], None)
    t3 = e6.summary(_row(3, "follow", by="offered", said=2), [], [], None)
    agg = e6.aggregate([{"id": "a", "turns": [t1, t2, t3]}])
    assert agg["e6_2"]["n"] == 2 and agg["e6_2"]["kinds"] == {"talk": 1, "follow": 1}
    assert len(agg["e6_3"]) == 1 and agg["e6_3"][0]["hub_requests"] == {"resolve": 1}
    assert agg["e6_4"] == {"close_line_written": 2, "turns": 3, "ask_said_zero": 1}
    assert agg["e6_5"]["voice_n"] == 2 and agg["e6_5"]["null_close_ends_with_question"] == 1


def test_off_state_does_not_run(tmp_path):
    out = asyncio.run(e6.run_e6(make_settings(tmp_path, chat_state="off")))
    assert out == {"ran": False, "why": "chat_state_off"}


def test_e6_py_no_hangul():
    body = re.sub(r'(?s)""".*?"""', "", (ROOT / "clinic_chat" / "consult" / "e6.py").read_text(encoding="utf-8"))
    body = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    assert not HANGUL.search(body)
