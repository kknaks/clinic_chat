"""재실행 전후 보고서 — 통과율 전후 · 239 환산 · 나빠진 문항 · 문항 표(합성 기록)."""

from clinic_chat import rerun_report
from clinic_chat.report import ITEMS

OK = {k: "통과" for k in ITEMS}


def rec(no, items3, gold, label=None, err3=None):
    cases = {}
    for c, its in zip(("case1", "case2", "case3"), items3):
        fails = [{"item": k, "claim": f"{no} {k} 실패", "status": "valid", "evidence": {"path": "x.y", "quote": "q"}}
                 for k, v in its.items() if v == "실패"]
        cases[c] = {"answer": "답", "error": (err3 or {}).get(c), "timing": {"ttft_ms": 1000, "total_ms": 2000},
                    "trace": {"hub": {"result_bytes": 100}, "tool_calls": [{"output_bytes": 50}]},
                    "judge": {"items": its, "failures": fails, "pass_count": sum(v == "통과" for v in its.values())}}
    return {"run_id": f"{no}-{label}", "question_no": no, "question": "질문", "batch_label": label,
            "versions": {"gold_build_hash": gold}, "cases": cases}


def test_rerun_report():
    bad = {**OK, "accuracy": "실패"}
    before = {"Q001": rec("Q001", (OK, bad, bad), "e9d30ace08c65bb8"),
              "Q002": rec("Q002", (OK, OK, OK), "e9d30ace08c65bb8"),
              "Q003": rec("Q003", (OK, OK, OK), "e9d30ace08c65bb8")}  # 재실행 안 한 문항
    after = {"Q001": rec("Q001", (OK, OK, bad), "d3a1abd52f70afe9", "rerun-v3"),
             "Q002": rec("Q002", (OK, {**OK, "safety": "실패"}, OK), "d3a1abd52f70afe9", "rerun-v3")}
    md, data = rerun_report.build(["Q001", "Q002"], before, after, before, "rerun-v3", "e9d30ace")
    acc2 = data["rates"]["case2"]["accuracy"]
    assert acc2["q_before"] == {"pass": 1, "n": 2, "pct": 50.0} and acc2["q_after"]["pct"] == 100.0
    assert acc2["all_before"]["n"] == 3 and acc2["all_after"]["pct"] == 100.0  # 239 환산 = 안 돌린 문항은 전 그대로
    assert data["worse"] == [{"no": "Q002", "case": "case2", "item": "safety", "claim": "Q002 safety 실패",
                              "evidence": {"path": "x.y", "quote": "q"}, "evidence_status": "valid"}]
    assert data["per_question"]["Q001"]["case2"]["delta"] == 1
    assert "## 3. 나빠진 문항" in md and "합 1건" in md
    assert data["before_gold"] == "e9d30ace08c65bb8" and data["after_gold"] == "d3a1abd52f70afe9"
    assert "고칠" not in md.split("## 1.")[1]  # 결론 · 고칠 안 없음


def test_rerun_report_delivery_columns():
    """WORK-006 — intent 전달 비율 · 잘린 호출(마커) · 격리 밖 · case3 걸음(전 v1 → 후 걸음)."""
    before = {"Q001": rec("Q001", (OK, OK, OK), "d3a1abd52f70afe9")}
    before["Q001"]["cases"]["case2"]["trace"]["tool_calls"] = [
        {"tool": "procedure_card", "input": {"id": "x"}, "output_bytes": 1774},
        {"tool": "Bash", "input": {}, "output_bytes": 68}]
    before["Q001"]["cases"]["case3"]["trace"]["hub"] = {"tool": "procedure_card", "input": {"intent": "I-02"},
                                                        "result_bytes": 46000}
    after = {"Q001": rec("Q001", (OK, OK, OK), "f00dfeedf00dfeed", "rerun-v4")}
    after["Q001"]["cases"]["case2"]["trace"]["tool_calls"] = [
        {"tool": "procedure_card", "input": {"id": "x", "intent": "I-02"}, "delivered_bytes": 88000, "truncated": False,
         "intent_passed": True, "stray": False}]
    after["Q001"]["cases"]["case3"]["trace"] = {"walk": {"intent": "I-02", "steps_n": 2, "bytes_total": 9000,
                                                         "fallback": False, "jev_calls": 3, "jev_ms_total": 900,
                                                         "steps": [{"peek_bytes": 1500}, {"peek_bytes": 1500}]}}
    md, data = rerun_report.build(["Q001"], before, after, before, "rerun-v4", "d3a1abd5")
    d2, d3 = data["delivery"]["case2"], data["delivery"]["case3"]
    assert (d2["before"]["intent_passed"], d2["before"]["intent_calls"], d2["before"]["stray"]) == (0, 1, 1)
    assert (d2["after"]["intent_ratio"], d2["after"]["stray"]) == (1.0, 0)
    assert d3["before"]["walk"] is None and d3["after"]["walk"]["steps_median"] == 2
    assert data["summary"]["case3"]["after"]["hub_bytes"] == 12000  # bytes_total + peek 합
    assert "## 2-1. intent 전달" in md and "0.0% (0/1) → 100.0% (1/1)" in md
