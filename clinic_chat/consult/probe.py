"""상담 CLI 실측기(WORK-011 §5 WP-0 · §15 WP-10) — CLI 판을 올릴 때마다 다시 돈다(SPEC-004 O-5).

    uv run python -m clinic_chat.consult.probe --e1   # 세션 A 3턴 + 다른 cwd resume · 세션 B(F7)
    uv run python -m clinic_chat.consult.probe --e2   # 세션 C — 답 중 끊은 뒤 resume
    uv run python -m clinic_chat.consult.probe --e3   # 서버 없이 실물 한 턴 흐름 세 번(허브 · Jev · CLI)
    CHAT_STATE=on uv run python -m clinic_chat.consult.probe --e5 [--only ex1,ex2]   # 0.2 실측 E5-1 ~ E5-4(켠 상태만)

글은 회사 자료 · 시술 자료가 없는 짧은 영어다(E1 · E2). 결과 = 터미널 JSON 한 덩이 + `CHAT_DIR/probe/<시각>.json`.
세션 파일은 「있음 / 없음」 만 적는다(경로를 적지 않는다) · 지우지 않는다(CL9-2).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from clinic_chat.config import Settings, load_settings
from clinic_chat.llm import auth
from clinic_chat.llm.chat import new_session_id
from clinic_chat.llm.chat_cli import ChatCliSpec, InitWatch, chat_args
from clinic_chat.llm.cli import ClaudeIngest, CliSpec, new_result
from clinic_chat.llm.cli_process import StopRun, child_env, run_cli
from clinic_chat.timing import Timer

SYS_A = "You are a terse assistant. Answer in at most one short sentence."
SYS_X = "You are a terse assistant. Always end every reply with the exact tag [ALPHA]."
SYS_Y = "You are a terse assistant. Always end every reply with the exact tag [OMEGA]."


def config_dir() -> Path:
    v = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(v).expanduser() if v else Path.home() / ".claude"


def session_file(session_id: str) -> Path | None:
    hits = list((config_dir() / "projects").glob(f"*/{session_id}.jsonl"))
    return hits[0] if hits else None


def encoded(cwd: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd.resolve()))


def session_seen(session_id: str, cwd: Path) -> dict:
    f = session_file(session_id)
    return {"exists": f is not None, "in_cwd_project": bool(f and f.parent.name == encoded(cwd))}


async def call(spec: ChatCliSpec, sid: str, mode: str, system: str, user: str, cwd: Path,
               kill_after_deltas: int | None = None) -> dict:
    timer = Timer()
    res = new_result(CliSpec(spec.bin, spec.model, spec.effort, spec.timeout_s))
    watch = InitWatch(ClaudeIngest(res, timer, None), sid)
    deltas = 0

    def on_line(raw: str) -> None:
        nonlocal deltas
        watch.line(raw)
        if kill_after_deltas is not None and '"text_delta"' in raw:
            deltas += 1
            if deltas >= kill_after_deltas:
                raise StopRun

    t = time.perf_counter()
    proc = await run_cli(chat_args(spec, sid, mode, system), cwd=str(cwd), env=child_env(), timeout_s=spec.timeout_s,
                         on_line=on_line, stdin_text=user)
    timer.finish()
    return {"mode": mode, "returncode": proc.returncode, "killed": proc.killed, "timed_out": proc.timed_out,
            "stderr_tail": [ln[:200].replace(str(Path.home()), "~") for ln in proc.stderr_tail],
            "error": res["error"], "result_subtype": res["cli"].get("subtype"), "num_turns": res["cli"].get("num_turns"),
            "answer": res["answer"][:300], "ttft_ms": timer.ttft_ms,
            "total_ms": round((time.perf_counter() - t) * 1000, 1), "tokens": res["tokens"],
            "thinking_seen": res["thinking_seen"], "observed_model": res["observed_model"],
            "observed_models": res["observed_models"], "init_session_matches": watch.seen == sid and not watch.mismatch,
            "init_keys": watch.init_keys, "cli_version": res["cli"].get("cli_version")}


def _has(text: str, word: str) -> bool:
    return word.lower() in (text or "").lower()


async def e1(spec: ChatCliSpec) -> dict:
    cwd = spec.cwd / "probe"
    cwd.mkdir(parents=True, exist_ok=True)
    other = spec.cwd / "probe-other"
    other.mkdir(parents=True, exist_ok=True)
    a = new_session_id()
    turns = [await call(spec, a, "new", SYS_A, "Remember the code word PELICAN. Reply only: OK.", cwd)]
    turns.append(await call(spec, a, "resume", SYS_A, "What code word did I ask you to remember? Reply with the word only.", cwd))
    turns.append(await call(spec, a, "resume", SYS_A, "Spell that code word backwards, letters only.", cwd))
    file_a = session_seen(a, cwd)
    fourth = await call(spec, a, "resume", SYS_A, "Repeat the code word once more, word only.", other)
    b = new_session_id()
    b1 = await call(spec, b, "new", SYS_X, "Say hello.", cwd)
    b2 = await call(spec, b, "resume", SYS_Y, "Say goodbye.", cwd)
    return {
        "A": {"turns": turns, "session_file": file_a,
              "max_turns_accepted": all(t["returncode"] == 0 and not t["error"] for t in turns),
              "subtype_not_limit": all(t["result_subtype"] == "success" for t in turns),
              "memory_turn2": _has(turns[1]["answer"], "pelican"), "memory_turn3": _has(turns[2]["answer"], "nacilep")},
        "A_other_cwd": {**fourth, "memory": _has(fourth["answer"], "pelican"),
                        "session_file_in_other": session_seen(a, other)["in_cwd_project"]},
        "B": {"turns": [b1, b2], "turn1_alpha": _has(b1["answer"], "[alpha]"),
              "turn2_alpha": _has(b2["answer"], "[alpha]"), "turn2_omega": _has(b2["answer"], "[omega]")},
    }


async def e2(spec: ChatCliSpec) -> dict:
    cwd = spec.cwd / "probe"
    cwd.mkdir(parents=True, exist_ok=True)
    c = new_session_id()
    c1 = await call(spec, c, "new", SYS_A, "Remember the number 4711. Reply only: OK.", cwd)
    c2 = await call(spec, c, "resume", SYS_A, "Count from 1 to 80 in words, separated by commas.", cwd, kill_after_deltas=3)
    f = session_file(c)
    after_kill = {"question_in_file": False, "partial_answer_in_file": False}
    if f:
        text = f.read_text(encoding="utf-8", errors="replace")
        after_kill = {"question_in_file": "Count from 1 to 80" in text,
                      "partial_answer_in_file": bool(re.search(r"\bone\b.*\btwo\b", text, re.I))}
    c3 = await call(spec, c, "resume", SYS_A,
                    "What number did I ask you to remember, and what did my previous message ask? Answer briefly.", cwd)
    return {"C": {"turns": [c1, c2, c3], "after_kill": after_kill,
                  "resume_after_kill_ok": c3["returncode"] == 0 and not c3["error"],
                  "remembers_number": _has(c3["answer"], "4711"), "mentions_count": _has(c3["answer"], "count")}}


def save(settings: Settings, out: dict) -> Path:
    d = settings.chat_dir_path() / "probe"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


async def amain(args: argparse.Namespace) -> dict:
    s = load_settings()
    spec = ChatCliSpec.from_settings(s)
    a = await auth.check(spec.bin, force=True)
    out: dict = {"auth": a.kind, "cli_version": a.version, "model": spec.model, "effort": spec.effort}
    if not a.ok:
        out["ran"] = False
        return out
    if args.e1:
        out["e1"] = await e1(spec)
    if args.e2:
        out["e2"] = await e2(spec)
    if args.e3:
        from clinic_chat.consult.e3 import run_e3

        out["e3"] = await run_e3(s)
    if getattr(args, "e5", False):
        from clinic_chat.consult.e5 import run_e5

        out["e5"] = await run_e5(s, [x for x in (args.only or "").split(",") if x] or None)
    out["ran"] = True
    out["saved"] = save(s, out).name
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="clinic_chat.consult.probe")
    ap.add_argument("--e1", action="store_true")
    ap.add_argument("--e2", action="store_true")
    ap.add_argument("--e3", action="store_true")
    ap.add_argument("--e5", action="store_true", help="0.2 E5 (needs CHAT_STATE=on in the process env)")
    ap.add_argument("--only", default="", help="--e5 conversation ids, comma separated")
    args = ap.parse_args()
    if not (args.e1 or args.e2 or args.e3 or args.e5):
        ap.print_help()
        return
    print(json.dumps(asyncio.run(amain(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
