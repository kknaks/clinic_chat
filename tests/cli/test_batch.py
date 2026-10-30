"""일괄 실행 — 설정 비면 시작 안 함 · 순서(질문 → provider 번갈아) · 재시도 · 한도 멈춤 · 이어 가기(모의 실행기)."""

from __future__ import annotations

import pytest

from clinic_chat import batch
from clinic_chat.runs import RunStore
from tests.conftest import make_settings

MD = """# 질문
| # | 질문 | 볼 것 |
|---|---|---|
| 1 | 이마 주름이 신경 쓰이는데 어떤 시술이 좋아요? | x |
| 2 | 팔자주름 개선하려면 뭘 받아야 해요? | |
## 다른 절
| # | 질문 | 측정 문항 |
|---|---|---|
| 15 | 울쎄라랑 써마지 차이가 뭐예요? | Q065 |
"""


def rec(run_id, *, errors=None, limited=False, resets=None):
    cli = {"rate_limited": True, "rate_limits": [{"resetsAt": resets}] if resets else []} if limited else {}
    return {"run_id": run_id, "cases": {c: {"error": (errors or {}).get(c), "trace": {"cli": cli}}
                                        for c in ("case1", "case2", "case3")}}


def settings(tmp_path, **kw):
    base = {"batch_gap_sec": 5, "batch_retries": 2, "batch_retry_backoff_sec": "60,180", "batch_limit_wait_sec": 1800}
    return make_settings(tmp_path, **{**base, **kw})


def test_parse_questions():
    qs = batch.parse_questions(MD)
    assert [q["n"] for q in qs] == [1, 2, 15] and qs[2]["text"] == "울쎄라랑 써마지 차이가 뭐예요?"


def test_plan_order_alternates_providers():
    plan = batch.make_plan(batch.parse_questions(MD), ["claude-cli", "codex-cli"], pick=[1, 15])
    assert [i["key"] for i in plan["items"]] == ["1:claude-cli", "1:codex-cli", "15:claude-cli", "15:codex-cli"]
    assert plan["progress"]["todo"] == 4 and plan["state"] == "planned"
    with pytest.raises(batch.BatchConfigError):
        batch.make_plan([], ["gpt"])


async def test_refuses_without_user_values(tmp_path):
    s = make_settings(tmp_path)  # 기본값 = 비움
    assert set(batch.config_problems(s)) == {"BATCH_GAP_SEC", "BATCH_RETRIES", "BATCH_LIMIT_WAIT_SEC"}
    plan = batch.make_plan(batch.parse_questions(MD), ["claude-cli"])
    called = []

    async def ex(*a):
        called.append(a)
        return rec("x")

    with pytest.raises(batch.BatchConfigError):
        await batch.run_batch(plan, settings=s, bstore=batch.BatchStore(tmp_path), execute=ex)
    assert called == []
    assert "BATCH_RETRY_BACKOFF_SEC(재시도 2번 만큼)" in batch.config_problems(settings(tmp_path, batch_retry_backoff_sec=""))


async def test_run_retry_limit_and_resume(tmp_path):
    s = settings(tmp_path)
    clock = {"t": 1000.0}
    slept: list[float] = []

    async def sleep(x):
        slept.append(x)
        clock["t"] += x

    seq = {
        "1:claude-cli": [rec("r1", errors={c: "시간 초과(600초)" for c in ("case1", "case2", "case3")}), rec("r1b")],
        "1:codex-cli": [rec("r2", limited=True), rec("r2b")],
        "2:claude-cli": [rec("r3", errors={"case3": "거절"})],  # 일부 오류 · 결과 — 재시도 안 함
        "2:codex-cli": [rec("r4")],
    }
    calls: list[str] = []

    async def ex(q, provider, extra):
        key = extra["batch_item"]
        calls.append(key)
        return seq[key].pop(0)

    plan = batch.make_plan(batch.parse_questions(MD), ["claude-cli", "codex-cli"], pick=[1, 2])
    bs = batch.BatchStore(tmp_path)
    out = await batch.run_batch(plan, settings=s, bstore=bs, execute=ex, sleep=sleep, now=lambda: clock["t"])
    assert out["state"] == "done" and out["progress"]["done"] == 4
    assert calls == ["1:claude-cli", "1:claude-cli", "1:codex-cli", "2:claude-cli", "1:codex-cli", "2:codex-cli"]
    assert 60 in slept  # 재시도 간격(첫 번째)
    assert any(x >= 1800 - 5 for x in slept)  # codex 한도 — resetsAt 모름 → BATCH_LIMIT_WAIT_SEC 잠듦
    items = {i["key"]: i for i in out["items"]}
    assert items["1:claude-cli"]["attempts"] == 2 and items["1:codex-cli"]["attempts"] == 1
    assert bs.load(out["batch_id"])["progress"]["done"] == 4 and bs.list()[0]["batch_id"] == out["batch_id"]


async def test_retry_exhausted_marks_failed(tmp_path):
    s = settings(tmp_path, batch_retries=1, batch_retry_backoff_sec="1")

    async def ex(q, p, extra):
        return rec("x", errors={c: "claude-cli 종료 코드 1" for c in ("case1", "case2", "case3")})

    async def sleep(x):
        pass

    plan = batch.make_plan(batch.parse_questions(MD), ["claude-cli"], pick=[1])
    out = await batch.run_batch(plan, settings=s, bstore=batch.BatchStore(tmp_path), execute=ex, sleep=sleep)
    assert out["items"][0]["status"] == "failed" and out["items"][0]["attempts"] == 2


def test_resume_running_items(tmp_path):
    rs = RunStore(tmp_path / "runs")
    rs.append_run({"run_id": "done1", "cases": {}})
    plan = batch.make_plan(batch.parse_questions(MD), ["claude-cli"], pick=[1, 2])
    plan["items"][0].update(status="running", run_id="done1")
    plan["items"][1].update(status="running", run_id="lost")
    batch.resume_fix(plan, rs)
    assert [i["status"] for i in plan["items"]] == ["done", "todo"]


QSET = {"version": 1, "n": 3, "questions": [
    {"no": "Q001", "src_id": "E01", "topic": "고민 → 추천 시술", "question": "이마 주름이 신경 쓰여요?",
     "reference_answer": "참고 답 1", "source_kind": "기존 평가", "group": "1"},
    {"no": "Q002", "src_id": "E02", "topic": "t", "question": "팔자주름?", "reference_answer": "참고 답 2",
     "source_kind": "x", "group": "1"},
    {"no": "Q010", "src_id": "S1", "topic": "t", "question": "울쎄라?", "reference_answer": "참고 답 10",
     "source_kind": "x", "group": "2"}]}


def test_question_set_meta_pick_and_skip(tmp_path):
    p = tmp_path / "qs.json"
    p.write_text(__import__("json").dumps(QSET, ensure_ascii=False), encoding="utf-8")
    s = make_settings(tmp_path, question_set_json=str(p))
    qs = batch.load_questions(s)
    assert [q["n"] for q in qs] == ["Q001", "Q002", "Q010"]
    assert qs[0]["meta"] == {"question_no": "Q001", "src_id": "E01", "topic": "고민 → 추천 시술",
                             "reference_answer": "참고 답 1", "source_kind": "기존 평가", "group": "1"}
    assert batch.parse_pick("Q001-Q003,Q010") == ["Q001", "Q002", "Q003", "Q010"]
    assert batch.parse_pick("1,3") == [1, 3] and batch.parse_pick("") is None
    plan = batch.make_plan(qs, ["claude-cli"], batch.parse_pick("Q001-Q003"), skip={"Q002"})
    assert [i["key"] for i in plan["items"]] == ["Q001:claude-cli"] and plan["items"][0]["meta"]["src_id"] == "E01"


async def test_meta_reaches_record_and_progress_report(tmp_path):
    p = tmp_path / "qs.json"
    p.write_text(__import__("json").dumps(QSET, ensure_ascii=False), encoding="utf-8")
    s = settings(tmp_path, question_set_json=str(p))
    clock = {"t": 0.0}
    seen: list[dict] = []
    reports: list[dict] = []

    async def ex(q, provider, extra):
        seen.append(extra)
        clock["t"] += 1000  # 판마다 1000초
        return {**rec(f"r{len(seen)}"), **extra}

    async def sleep(x):
        clock["t"] += x

    plan = batch.make_plan(batch.load_questions(s), ["claude-cli"])
    out = await batch.run_batch(plan, settings=s, bstore=batch.BatchStore(tmp_path), execute=ex, sleep=sleep,
                                now=lambda: clock["t"], report=reports.append, report_every=1800)
    assert out["progress"]["done"] == 3
    assert [x["question_no"] for x in seen] == ["Q001", "Q002", "Q010"]
    assert seen[0]["reference_answer"] == "참고 답 1" and seen[0]["topic"] == "고민 → 추천 시술"
    assert reports and {"progress", "now"} <= set(reports[0])  # 30분(1800초)마다 한 줄
