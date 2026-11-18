"""상담 CLI 실행기(SPEC-004 §3-2 CL1 ~ CL7 · §3-3 CL9 · §3-4 CL10 ~ CL12) — 첫 턴 `--session-id` → 다음 턴 `--resume`.

- 인자는 `chat_args` 하나가 짓는다: 첫 턴과 다음 턴은 `--session-id <UUID>` ↔ `--resume <UUID>` 말고 **글자 그대로 같다**.
  시스템 글은 `--system-prompt "<글>"`(WORK-011 §3 — 프롬프트 JSON 을 이어 붙인 글) · 프롬프트(턴 메시지)는 stdin.
- 격리 인자는 `llm/cli.py` `claude_args` 와 같은 꼴에서 `--no-session-persistence` 만 뺐다(CL4 · CL5).
- 고정 cwd(`CHAT_CLI_CWD` — 레포 밖 · 턴 뒤에 지우지 않는다 · CL9).
- stream-json 은 `ClaudeIngest` 로 읽되 init 줄을 한 번 더 본다 — 세션 id 가 준 값과 다르면 그 턴은 오류(CL7).
- 오류는 `error{kind, stage, detail}` 코드로만 낸다(사람에게 보일 문장 0 — ST4). `detail` 에서 홈 경로 · cwd 는 지운다(LG5).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path

from clinic_chat import progress
from clinic_chat.llm import auth
from clinic_chat.llm.chat import ChatConv, chat_error, new_chat_result, new_session_id
from clinic_chat.llm.cli import DISALLOWED_TOOLS, ClaudeIngest, CliSpec, new_result
from clinic_chat.llm.cli_process import StopRun, child_env, run_cli
from clinic_chat.timing import Timer

# E1 ⑥(WP-0 실측 — init 줄이 세션 id 를 주는 키)
SESSION_KEY = "session_id"
# CL4 — 상담 인자에 없어야 하는 것
FORBIDDEN_FLAGS = ("--no-session-persistence", "--continue", "-c", "--mcp-config", "--max-budget-usd", "--bare",
                   "--json-schema", "--system-prompt-file")


@dataclass
class ChatCliSpec:
    bin: str
    model: str
    fallback_model: str
    effort: str
    timeout_s: float
    cwd: Path
    max_concurrent: int = 1

    @classmethod
    def from_settings(cls, s) -> ChatCliSpec:
        return cls(s.claude_cli_bin, s.chat_model, s.chat_fallback_model, s.chat_effort, s.chat_turn_timeout_sec,
                   s.chat_cli_cwd_path(), s.cli_max_concurrent)


def chat_args(spec: ChatCliSpec, session_id: str, mode: str, system_text: str) -> list[str]:
    """CL2 · CL3 — `mode` = `new`(`--session-id`) · `resume`(`--resume`)."""
    head = ["--session-id", session_id] if mode == "new" else ["--resume", session_id]
    return [spec.bin, "-p", *head,
            "--model", spec.model, "--fallback-model", spec.fallback_model, "--effort", spec.effort,
            "--max-turns", "1", "--system-prompt", system_text,
            "--setting-sources", "", "--tools", "", "--disallowedTools", DISALLOWED_TOOLS,
            "--disable-slash-commands", "--strict-mcp-config", "--permission-mode", "bypassPermissions",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages"]


def scrub(text: str | None, *paths: Path | str | None) -> str | None:
    """LG5 — 홈 경로 · cwd 를 글에서 지운다."""
    if not text:
        return text
    out = text
    for p in sorted({str(x) for x in paths if x}, key=len, reverse=True):
        out = out.replace(p, "<path>")
    return out.replace(str(Path.home()), "~")


_SEMS: dict[tuple[int, int], asyncio.Semaphore] = {}


def _semaphore(n: int) -> asyncio.Semaphore:
    """앱 전체 CLI 동시 수(CL12 ② — `cli.py` 의 꼴을 상담 쪽에)."""
    key = (id(asyncio.get_running_loop()), n)
    if key not in _SEMS:
        _SEMS[key] = asyncio.Semaphore(max(1, n))
    return _SEMS[key]


def _error_kind(msg: str) -> str:
    """`ClaudeIngest` 가 `error` 에 적는 글 → 코드."""
    if msg.startswith("구독 아님"):
        return "not_subscription"
    if msg.startswith("한도"):
        return "rate_limit"
    return "cli_result"


class InitWatch:
    """init 줄의 세션 id 를 준 값과 대조한다(CL7) — 다르면 실행을 멈춘다."""

    def __init__(self, ingest: ClaudeIngest, expect: str) -> None:
        self.ingest, self.expect = ingest, expect
        self.seen: str | None = None
        self.mismatch = False
        self.init_keys: list[str] = []

    def line(self, raw: str) -> None:
        try:
            ev = json.loads(raw)
        except ValueError:
            ev = None
        if isinstance(ev, dict) and ev.get("type") == "system" and ev.get("subtype") == "init":
            self.init_keys = sorted(ev)
            self.seen = ev.get(SESSION_KEY)
            if self.seen != self.expect:
                self.mismatch = True
                raise StopRun
        self.ingest.line(raw)


class ChatCli:
    """`ChatLLM` 의 CLI 구현."""

    kind = "cli"

    def __init__(self, spec: ChatCliSpec) -> None:
        self.spec = spec

    async def turn(self, conv: ChatConv, user_message: str, prompt, history: list[dict]) -> dict:
        res = new_chat_result()
        mode = "new" if conv.state == "new" else "resume"
        res["llm"].update({"kind": self.kind, "session_id": conv.session_id, "mode": mode,
                           "model": self.spec.model, "effort": self.spec.effort})
        if conv.state == "broken" or conv.running:  # CL12 ① — 겹친 턴은 받지 않는다(CLI 를 띄우지 않는다)
            res["error"] = chat_error("broken" if conv.state == "broken" else "busy", "precheck")
            res["session_state"] = conv.state
            return res
        conv.running = True
        spawned = False
        try:
            a = await auth.check(self.spec.bin)
            res["llm"]["cli_version"] = a.version
            if not a.ok:
                res["error"] = chat_error("auth", "precheck", a.kind)
                return res
            sem = _semaphore(self.spec.max_concurrent)
            if sem.locked():
                progress.emit("step", step="llm_wait")
            async with sem:
                progress.emit("step", step="llm")
                spawned = await self._spawn(conv, mode, user_message, prompt.system_text, res)
            return res
        finally:
            conv.running = False
            self._move(conv, mode, res, spawned)
            res["session_state"] = conv.state

    async def _spawn(self, conv: ChatConv, mode: str, user_message: str, system_text: str, res: dict) -> bool:
        spec = self.spec
        timer = Timer()
        ing_res = new_result(CliSpec(spec.bin, spec.model, spec.effort, spec.timeout_s))
        watch = InitWatch(ClaudeIngest(ing_res, timer, None), conv.session_id)
        t = time.perf_counter()
        spawned = True
        try:
            spec.cwd.mkdir(parents=True, exist_ok=True)  # CL9 — 없으면 만든다 · 지우지 않는다
            proc = await run_cli(chat_args(spec, conv.session_id, mode, system_text), cwd=str(spec.cwd),
                                 env=child_env(), timeout_s=spec.timeout_s, on_line=watch.line,
                                 stdin_text=user_message)
            res["llm"]["returncode"] = proc.returncode
            if watch.mismatch:
                res["error"] = chat_error("session_mismatch", "stream", scrub(str(watch.seen), spec.cwd))
            elif ing_res["error"]:
                res["error"] = chat_error(_error_kind(ing_res["error"]), "stream", scrub(ing_res["error"], spec.cwd))
            elif proc.timed_out:
                res["error"] = chat_error("timeout", "stream", f"{spec.timeout_s:g}")
            elif proc.returncode not in (0, None):
                res["error"] = chat_error("exit", "stream",
                                          scrub(f"{proc.returncode}: {' | '.join(proc.stderr_tail)[:300]}", spec.cwd))
            elif ing_res["tool_calls"]:
                res["error"] = chat_error("tool_call", "stream", ",".join(c["tool"] for c in ing_res["tool_calls"]))
        except (FileNotFoundError, PermissionError) as e:
            spawned = False
            res["error"] = chat_error("spawn", "precheck", type(e).__name__)
        except Exception as e:  # noqa: BLE001 — 그 턴만 오류
            res["error"] = chat_error("internal", "stream", scrub(f"{type(e).__name__}: {e}"[:300], spec.cwd))
        finally:
            timer.finish()
        res["answer_raw"] = ing_res["answer"]
        res["tokens"] = ing_res["tokens"]
        res["observed_model"] = ing_res["observed_model"]
        res["observed_models"] = ing_res["observed_models"]
        res["thinking_seen"] = ing_res["thinking_seen"]
        res["stop_reason"] = ing_res["stop_reason"]
        res["timing"] = {"ttft_ms": timer.ttft_ms, "total_ms": round((time.perf_counter() - t) * 1000, 1)}
        res["llm"]["cli_version"] = ing_res["cli"].get("cli_version") or res["llm"]["cli_version"]
        res["llm"]["result_subtype"] = ing_res["cli"].get("subtype")
        return spawned

    @staticmethod
    def _move(conv: ChatConv, mode: str, res: dict, spawned: bool) -> None:
        """CL10 — 세션 상태 옮김."""
        err = res["error"]
        if err and err["kind"] in ("busy", "broken"):
            return
        if not err:
            conv.state = "open"
        elif mode == "new":
            conv.session_id = new_session_id()  # ① 같은 UUID 를 다시 `--session-id` 로 주지 않는다
        elif spawned:
            conv.state = "broken"  # ② 답이 끝나기 전 실패
        # ③ open 에서 띄우기 전 실패 → open 그대로
