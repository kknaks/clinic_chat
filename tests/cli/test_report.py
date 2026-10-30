"""보고서 — 통과율 · 사유 분류 · case1 통과 / 골드 실패 · 모름 답(합성 기록)."""

from clinic_chat import report


def case(items, failures=(), answer="답", error=None):
    return {"answer": answer, "error": error, "timing": {"ttft_ms": 1000, "total_ms": 2000},
            "judge": {"items": items, "failures": list(failures), "pass_count": sum(v == "통과" for v in items.values())},
            "word_check": {"not_found": 0, "hub_names": 1, "names": []}, "trace": {}}


OK = {k: "통과" for k in report.ITEMS}


def test_report_sections():
    f_unknown = {"item": "safety", "claim": "「정보 없음」이라 했지만 원장에 있음", "status": "valid",
                 "evidence": {"path": "x.y", "quote": "주의"}}
    f_link = {"item": "accuracy", "claim": "연결이 틀림", "status": "invalid", "evidence": {"path": "a", "quote": "b"}}
    runs = [{"run_id": "r1", "question_no": "Q001", "question": "이마 주름?", "provider": "claude-cli",
             "versions": {"answer_model": "m"},
             "cases": {"case1": case(OK), "case2": case({**OK, "accuracy": "실패"}, [f_link]),
                       "case3": case({**OK, "safety": "실패"}, [f_unknown], answer="부위 주의는 정보 없음입니다")}}]
    md = report.build(runs, [])
    assert "| case1 원장 MCP | 100% (1/1)" in md
    assert "### 「정보 없음」인데 원장엔 있음 — 1건" in md and "### 빠진 연결 — 1건" in md
    assert "⚠ 근거 무효" in md
    assert "합 2건" in md  # case1 통과 · 골드 실패(정확성 · 안전)
    assert "열마다 {'case3': 1}" in md
