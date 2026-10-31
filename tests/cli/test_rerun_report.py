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
