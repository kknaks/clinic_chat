"""채점 두 층 실행(SPEC-002 §8 · WORK-002 §9).

- 일반 채점 `--batch <batch_id>`: **사용자가 확인한(`confirmed`) 키로만** 돈다. 확인 전 키 · `synthetic: true` 키 · build 가
  다른 키는 거부한다 — 풀어 주는 플래그 · env · 설정이 없다(SPEC-002 §9-5 확인 절차 4). 결과는 `runs/quality.jsonl`.
- 배관 확인 `--plumbing`: 세트와 떨어진 진입점. **가짜 코드 · 가짜 이름**으로 질문 · 답 · 키를 스스로 만들어 임시 폴더에 쓰고
  ① → ② (실물 Opus 1회) → 보고서까지 한 번 잇는다. `EVAL_SET_DIR` · `runs.jsonl` · `QUESTIONS_CSV` · gold CSV 를 읽지 않는다.
  결과는 `runs/plumbing/` 에만 · 머리에 「합성 키 — 배관 확인 · 채점 수치 아님」.

    uv run python -m clinic_chat.quality.grade --batch <batch_id>
    uv run python -m clinic_chat.quality.grade --plumbing
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import tempfile
from pathlib import Path

from clinic_chat.clients.hub_rest import HubRest
from clinic_chat.config import Settings, load_settings
from clinic_chat.evalset.answer_key import key_head, load_key, script_sha
from clinic_chat.evalset.loader import load_set, read_questions_csv, set_dir
from clinic_chat.llm.cli import CliSpec
from clinic_chat.quality import checks, judge, ledger_diff
from clinic_chat.quality.gold_codes import GoldCodes, check_build, load
from clinic_chat.raw_ledger.raw_tools import RawStore, RawToolError
from clinic_chat.runs import RunStore, now_iso

PLUMBING_MARK = "합성 키 — 배관 확인 · 채점 수치 아님"


class KeyRejected(RuntimeError):
    pass


def accept_key(key: dict | None, build: dict | None, *, script_sha: str | None = None,
               calls_sha: str | None = None) -> dict:
    """일반 채점이 쓸 수 있는 키인가 — 없음 · 미확인 · 합성 · build 다름 · 스크립트 · 명세가 바뀜(N-2)은 전부 거부."""
    if not key:
        raise KeyRejected("답 키 없음")
    if key.get("synthetic"):
        raise KeyRejected("합성 키는 일반 채점에 쓰지 않는다")
    if not key.get("confirmed"):
        raise KeyRejected("사용자 확인 전 키(unconfirmed) — 채점하지 않는다")
    kb = key.get("build") or {}
    if build and (kb.get("gold_hash"), kb.get("aux_hash")) != (build.get("gold_hash"), build.get("aux_hash")):
        raise KeyRejected("키의 build 가 지금 허브와 다르다 — 키 무효")
    if script_sha and key.get("script_sha256") != script_sha:
        raise KeyRejected("답 키 스크립트가 키를 뽑은 뒤 바뀌었다 — 다시 뽑고 확인")
    if calls_sha and key.get("calls_sha256") != calls_sha:
        raise KeyRejected("호출 명세가 키를 뽑은 뒤 바뀌었다 — 다시 뽑고 확인")
    return key


async def key_row_values(hub: HubRest, key: dict) -> dict[str, dict]:
    """행 값 읽기(§9-5) — 키의 calls[] 를 같은 build 로 다시 불러 gid 로 고른다."""
    from clinic_chat.evalset.answer_key import read_rows

    _, _, _, build, raw = await read_rows(hub, key.get("calls") or [])
    kb = key.get("build") or {}
    if build and build.get("gold_hash") != kb.get("gold_hash"):
        raise KeyRejected("행 값 읽기의 build 가 키와 다르다")
    want = {r["gid"] for r in key.get("rows") or []}
    return {r["gid"]: r for r in raw if r.get("gid") in want}


def judge_spec(s: Settings) -> CliSpec:
    return CliSpec(s.claude_cli_bin, s.judge_model, s.claude_cli_effort, s.cli_timeout_sec, reasoning="on",
                   lowest_effort=s.lowest_effort)


async def grade_batch(s: Settings, batch_id: str) -> dict:
    store = RunStore(Path(s.path(s.runs_dir)))
    rows = [r for r in store.runs() if r.get("batch_id") == batch_id]
    hub = HubRest(s.hub_rest_url, page_limit=s.hub_page_limit)
    h = await hub.health()
    gold = load(s.path(s.gold_csv_dir))
    check_build(gold, h)
    sset, qcsv = load_set(s), read_questions_csv(s)
    lck = ledger_diff.load(s.path(s.ledger_check_path))
    raw = None
    try:
        raw = RawStore(s.raw_dir or "")
    except RawToolError:
        pass
    graded, rejected = 0, []
    for row in rows:
        sid = row.get("set_id")
        if not sid:
            continue
        cp = set_dir(s) / "calls" / f"{sid}.json"
        csha = hashlib.sha256(cp.read_bytes()).hexdigest() if cp.is_file() else None
        try:
            key = accept_key(load_key(s, sid), h.get("build"), script_sha=script_sha(), calls_sha=csha)
        except KeyRejected as e:
            rejected.append({"set_id": sid, "reason": str(e)})
            continue
        kv = await key_row_values(hub, key)
        item = sset.get(sid, {})
        named = set()
        if cp.is_file():  # N-8 — 질문이 이름 댄 케이스(명세 입구의 케이스 이름)는 D9 에서 뺀다
            named = {e.get("name") for e in json.loads(cp.read_text(encoding="utf-8")).get("entries") or []
                     if e.get("table") == "procedure_case" and e.get("name")}
        res = await grade_row(s, row, key, kv, gold, raw, item, (qcsv.get(sid) or {}).get("answer"), lck,
                              named_cases=named, graded_key_sha=(key_head(s, sid) or {}).get("sha256"))
        store.append_quality(res)
        graded += 1
    return {"batch_id": batch_id, "graded": graded, "rejected": len(rejected), "rejected_detail": rejected}


def _raw_exists(raw):
    def f(p: str) -> bool:
        try:
            raw.resolve(p)
            return True
        except Exception:  # noqa: BLE001
            return False
    return f if raw else None


async def grade_row(s: Settings, row: dict, key: dict, kv: dict, gold: GoldCodes, raw, item: dict,
                    sample_answer: str | None, lck_table: dict, synthetic: bool = False,
                    named_cases: set[str] | None = None, graded_key_sha: str | None = None) -> dict:
    cols = [k for k in ("a", "b", "b_on", "c") if row["columns"].get(k)]
    seed = random.SystemRandom().randrange(2**31)
    order = list(cols)
    random.Random(seed).shuffle(order)
    ids = ledger_diff.lck_ids(item, key)
    out = {"run_id": row["run_id"], "set_id": row.get("set_id"), "graded_at": now_iso(),
           "answer_key_sha": (row.get("answer_key") or {}).get("sha256"),
           "answer_key_sha_graded": graded_key_sha,  # N-3 — 채점에 쓴 키 파일(확인 뒤) sha · runs 줄 sha 는 batch 때 것
           "judge_order": {"columns": order, "seed": seed},
           "lck_ids": ids, "synthetic": synthetic, "columns": {}}
    for k in order:
        c = row["columns"][k]
        trace = c.get("trace") if isinstance(c.get("trace"), dict) else (row["columns"]["b"].get("trace") or {})
        d = checks.run(column=k, answer=c.get("answer") or "", cited=c.get("cited") or [], trace=trace, key=key,
                       key_rows=kv, gold=gold, raw_exists=_raw_exists(raw), end_state=c.get("end_state"),
                       expected_end_state=key.get("expected_end_state"), named_cases=named_cases)
        text = judge.judge_input(question=row["question"], answer=c.get("answer") or "", key_rows=list(kv.values()),
                                 expected_end_state=key.get("expected_end_state") or "",
                                 ledger_check=key.get("ledger_check") or [],
                                 lck_text=ledger_diff.rows_for(lck_table, ids), sample_answer=sample_answer,
                                 d_results={x: d[x] for x in ("D1", "D2")}, two_readings=bool(item.get("two_readings")),
                                 scope_gap=bool(item.get("expected_scope_gap")), kinds_met=key.get("kinds_met"))
        j = await judge.grade_one(judge_spec(s), text, s.cli_max_concurrent)
        out["columns"][k] = {"checks": d, "judge": j, "graded_end_state": j.get("graded_end_state"),
                             "followed_ledger_error": j.get("followed_ledger_error"),
                             "ends_with_question": d["ends_with_question"]}
    return out


# ───────────── E6 — 합성 키 배관 확인 ─────────────

def synthetic_inputs() -> dict:
    """가짜 코드 · 가짜 이름만 — 회사 데이터 없음(시험 fixture 와 같은 생성기)."""
    gold = GoldCodes()
    gold.gids = {"GPF-90001", "GIN-90001", "GIN-90002"}
    gold.codes = {"procedure:ZZP-901", "concern:ZZC-901", "procedure:ZZP-902"}
    gold.entry_codes = set(gold.codes)
    gold.names = {"가짜시술알파": {"procedure:ZZP-901"}, "가짜고민베타": {"concern:ZZC-901"},
                  "가짜시술감마": {"procedure:ZZP-902"}}
    key_rows = {
        "GIN-90001": {"gold_table": "g_indication", "gid": "GIN-90001", "candidate_table": "procedure",
                      "candidate_code": "ZZP-901", "candidate_name": "가짜시술알파", "concern_code": "ZZC-901",
                      "concern_name": "가짜고민베타", "reach": "시술 직접", "procedure_code": "ZZP-901"},
        "GIN-90002": {"gold_table": "g_indication", "gid": "GIN-90002", "candidate_table": "procedure",
                      "candidate_code": "ZZP-902", "candidate_name": "가짜시술감마", "concern_code": "ZZC-901",
                      "concern_name": "가짜고민베타", "reach": "제품 경유", "procedure_code": "ZZP-902"},
    }
    key = {"id": "SYN-01", "synthetic": True, "build": {"gold_hash": "synthetic", "aux_hash": "synthetic"},
           "rows": [{"gold_table": "g_indication", "gid": g, "bucket": "groups", "layer_role": None, "hier_match": None,
                     "codes": [f"procedure:{r['candidate_code']}", "concern:ZZC-901"], "side_codes": []}
                    for g, r in key_rows.items()],
           "codes": ["procedure:ZZP-901", "concern:ZZC-901", "procedure:ZZP-902"], "unnamed_codes": [],
           "must_rows": [], "ledger_check": [], "expected_end_state": "근거 충족", "confirmed": None}
    row = {"run_id": "syn-run-01", "set_id": "SYN-01", "question": "가짜고민베타에는 무엇을 하나?",
           "answer_key": {"sha256": "synthetic", "confirmed": False},
           "columns": {"b": {"answer": "가짜고민베타에는 가짜시술알파(시술 직접)가 있다 [GIN-90001].",
                             "cited": ["GIN-90001"], "end_state": "근거 충족",
                             "trace": {"verified_gids": [{"gid": "GIN-90001", "depth": "breadth"},
                                                         {"gid": "GIN-90002", "depth": "breadth"}]}}}}
    return {"gold": gold, "key": key, "key_rows": key_rows, "row": row}


async def plumbing(s: Settings, out_dir: Path | None = None) -> dict:
    syn = synthetic_inputs()
    tmp = Path(tempfile.mkdtemp(prefix="clinic-chat-plumbing-"))
    for name, v in (("key.json", syn["key"]), ("row.json", syn["row"])):
        (tmp / name).write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    res = await grade_row(s, syn["row"], syn["key"], syn["key_rows"], syn["gold"], None, {}, None, {}, synthetic=True)
    res["mark"] = PLUMBING_MARK
    j = res["columns"]["b"]["judge"]
    shape = {"items_5": set(j.get("items", {})) == set(judge.ITEMS),
             "failure_evidence_field": all("evidence" in v or v.get("verdict") in ("pass", "na")
                                           for v in j.get("items", {}).values()),
             "graded_end_state": j.get("graded_end_state") is not None, "judge_model": j.get("model"),
             "judge_error": j.get("error"), "judge_ms": j.get("ms")}
    od = out_dir or Path(s.path(s.runs_dir)) / "plumbing"
    od.mkdir(parents=True, exist_ok=True)
    (od / "quality.jsonl").open("a", encoding="utf-8").write(json.dumps(res, ensure_ascii=False) + "\n")
    from clinic_chat import report

    rpt = report.render([syn["row"]], [res], title=f"배관 확인 — {PLUMBING_MARK}", plumbing=True)
    (od / "report.md").write_text(rpt, encoding="utf-8")
    return {"mark": PLUMBING_MARK, "out": str(od), "shape": shape}


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.quality.grade")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--batch")
    g.add_argument("--plumbing", action="store_true")
    a = ap.parse_args()
    s = load_settings()
    if a.plumbing:
        r = asyncio.run(plumbing(s))
        print(PLUMBING_MARK)
        print(json.dumps(r, ensure_ascii=False))
        return
    print(json.dumps(asyncio.run(grade_batch(s, a.batch)), ensure_ascii=False))


if __name__ == "__main__":
    main()
