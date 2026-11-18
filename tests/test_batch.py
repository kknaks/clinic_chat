"""WP-8 — batch 6 문항 · 이어 돌리기 · 순서 seed · 차례 실행 · draft 건너뜀 · --check(모의 세 열)."""

from __future__ import annotations

import json

from clinic_chat.batch import check, run_batch
from clinic_chat.columns.base import Deps
from clinic_chat.runs import RunStore
from tests.conftest import make_settings, stream
from tests.fake_jev import FakeJev
from tests.walk_fixture import P1, cand, make_hub


def jev():
    def ch(name, crit, state):
        k = "procedure" if name == "range" else list(crit)[0]
        p = {x: 0.001 for x in crit}
        p[k] = 0.95
        return k, p
    return FakeJev(ch, lambda n, i, s: 0.9)


def setup(tmp_path, fake_cli, n=6, drafts=1):
    bin_, route, log = fake_cli
    (route / "answer.jsonl").write_text(stream("답"), encoding="utf-8")
    tools = stream("답").replace('"mcp_servers": []', '"mcp_servers": [{"name": "hub", "status": "connected"}, '
                                                       '{"name": "clinic-chat-raw", "status": "connected"}]')
    (route / "tools.jsonl").write_text(tools, encoding="utf-8")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "f.json").write_text("{}", encoding="utf-8")
    s = make_settings(tmp_path, claude_cli_bin=bin_, raw_dir=str(raw))
    es = tmp_path / "eval-set"
    es.mkdir()
    lines = [{"id": f"T{i}", "source": "추가", "question": f"가짜 질문 {i}", "status": "confirmed", "range": "시술"}
             for i in range(n)]
    lines += [{"id": f"D{i}", "source": "추가", "question": "초안", "status": "draft", "range": "시술"} for i in range(drafts)]
    (es / "set.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    return s, Deps(s, make_hub(resolve=[cand(P1)]).client(), jev().jev(s)), log


async def test_batch_six_resume_seed_sequential(tmp_path, fake_cli):
    s, d, log = setup(tmp_path, fake_cli)
    store = RunStore(tmp_path / "runs")
    msgs: list[str] = []
    r = await run_batch(s, d, label="t", store=store, log=msgs.append)
    assert r["ok"] and r["ran"] == 6 and r["draft_skipped"] == 1
    rows = store.runs()
    assert len(rows) == 6 and len({x["order"]["seed"] for x in rows}) == 6
    assert all(x["set_id"].startswith("T") for x in rows)
    # 차례로 — 한 줄 안의 열 시간 구간이 겹치지 않는다(CLI 프로세스가 동시에 안 뜸: 동시 상한 1 · 열 루프 await)
    assert all(set(x["columns"]) == {"a", "b", "b_on", "c"} for x in rows)
    assert all(x["run_mode"] == "sequential" for x in rows)  # batch 는 차례로 그대로(화면만 동시)
    r2 = await run_batch(s, d, label="t", store=store, resume=r["batch_id"], log=msgs.append)
    assert r2["ran"] == 0 and len(store.runs()) == 6
    assert any("draft 건너뜀 1" in m for m in msgs)


async def test_check_names_what_is_missing(tmp_path, fake_cli):
    s, d, _ = setup(tmp_path, fake_cli)
    c = await check(s, d)
    assert c["set"]["draft"] == 1 and c["answer_key"]["keys"] == 0 and c["prompts"]["confirmed"] is False
    s2 = make_settings(tmp_path, typesafe_api_key=None, raw_dir=None)
    from tests.fake_hub import FakeHub

    c2 = await check(s2, Deps(s2, FakeHub().client(), jev().jev(s)))
    assert not c2["hub"]["ok"] and not c2["jev_key"]["ok"] and not c2["cli"]["ok"] and not c2["raw"]["ok"]
    r = await run_batch(s2, Deps(s2, FakeHub().client(), jev().jev(s)), label="x", log=lambda m: None)
    assert r["ok"] is False and set(r["missing"]) >= {"hub", "jev_key", "cli", "raw"}
