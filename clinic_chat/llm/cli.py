"""`claude -p` 한 번 — stream-json 이벤트를 답 · 시간 · 도구 호출 · 토큰으로 옮긴다(SPEC-002 §6-1 · §6-2 · 옛 틀 Claude 갈래).

격리(세 열 같음 — SPEC-002 §2-2 · §6-2): `--setting-sources ""` · `--tools ""`(내장 도구 끔 — MCP 도구만) ·
`--disallowedTools` · `--disable-slash-commands` · `--strict-mcp-config` · `--no-session-persistence` ·
`--system-prompt`(갈아 끼움) · 임시 cwd(시스템 임시 폴더 · 실행마다 새로) · 프롬프트는 stdin.
자식 env = `child_env()` — API 키 · TYPESAFE 키 · 세트 · 채점 자리 env 를 지운다. 실행 앞에 구독인지 본다.

추론(SPEC-002 O-23 · `CliSpec.reasoning`):
- `on` → `--effort <batch effort>`(A · C · B 켬)
- `lowest_effort` → `--effort <가장 낮은 값>`
- `off` → `--effort <가장 낮은 값>` + `--settings {"alwaysThinkingEnabled": false}` + env `MAX_THINKING_TOKENS=0`
  (claude --help 에 「추론 끔」 플래그는 없다 — E2 에서 stream-json 에 thinking 블록이 없는지로 확인한 꼴)

실물 확인 E2 · E3:  uv run python -m clinic_chat.llm.cli --ping --model <sonnet|haiku> [--reasoning off]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from clinic_chat import progress
from clinic_chat.llm import auth
from clinic_chat.llm.cli_process import StopRun, child_env, run_cli
from clinic_chat.timing import Timer

MCP_PREFIX = "mcp__"
STRUCTURED_TOOL = "StructuredOutput"
DISALLOWED_TOOLS = "Bash,Read,Grep,Glob,Edit,Write,WebFetch,WebSearch,Task,NotebookEdit"
OFF_SETTINGS = {"alwaysThinkingEnabled": False}
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
CLI_TRUNC_MARKERS = ("exceeds maximum allowed tokens", "response was truncated", "output too large")

Reasoning = Literal["off", "lowest_effort", "on"]


@dataclass
class CliSpec:
    bin: str
    model: str
    effort: str
    timeout_s: float
    reasoning: Reasoning = "on"
    lowest_effort: str = "low"
    mcp_output_tokens: int | None = None

    def effort_arg(self) -> str:
        return self.effort if self.reasoning == "on" else self.lowest_effort


@dataclass
class McpAttach:
    name: str  # 서버 id(도구 이름 접두 mcp__<name>__)
    url: str | None = None  # http(허브 — C)
    command: str | None = None  # stdio(로컬 원장 — A)
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    tools: tuple[str, ...] = ()  # 주면 도구 이름 단위 허용(C = health 뺀 7 — O-15) · 비면 서버 단위

    def server(self) -> dict:
        if self.url:
            return {"type": "http", "url": self.url}
        return {"type": "stdio", "command": self.command, "args": self.args, "env": self.env}

    def allowed(self) -> list[str]:
        if self.tools:
            return [f"{MCP_PREFIX}{self.name}__{t}" for t in self.tools]
        return [f"{MCP_PREFIX}{self.name}"]


def claude_args(spec: CliSpec, system: str, mcp: McpAttach | None, schema: dict | None) -> list[str]:
    """프롬프트는 stdin 으로(`--allowedTools` · `--tools` 가 가변 인자라 뒤 위치 인자를 삼킴 — 옛 실물 확인)."""
    a = [spec.bin, "-p", "--model", spec.model, "--effort", spec.effort_arg(), "--system-prompt", system,
         "--setting-sources", "", "--tools", "", "--disallowedTools", DISALLOWED_TOOLS,
         "--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence",
         "--permission-mode", "bypassPermissions",
         "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    if spec.reasoning == "off":
        a += ["--settings", json.dumps(OFF_SETTINGS)]
    if mcp:
        a += ["--mcp-config", json.dumps({"mcpServers": {mcp.name: mcp.server()}}, ensure_ascii=False),
              "--allowedTools", ",".join(mcp.allowed())]
    if schema:
        a += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    return a


def cli_env(spec: CliSpec) -> dict[str, str]:
    extra = {"MAX_THINKING_TOKENS": "0"} if spec.reasoning == "off" else {}
    return child_env(extra, mcp_output_tokens=spec.mcp_output_tokens)


def new_result(spec: CliSpec) -> dict:
    return {"answer": "", "interim_text": [], "error": None, "structured": None, "tool_calls": [], "tokens": {},
            "token_rounds": [], "observed_model": None, "observed_models": None, "thinking_seen": False, "llm_round_trips": 0,
            "stop_reason": None, "timing": {}, "cost": {"billed": "subscription"},
            "cli": {"model": spec.model, "effort": spec.effort_arg(), "reasoning": spec.reasoning}}


def _result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return json.dumps(content, ensure_ascii=False)


class ClaudeIngest:
    """stream-json 줄 → 결과. 도구 결과 본문은 `tool_calls[].output_text`(기록에 남길지는 열이 정한다).

    답 = **마지막 도구 호출 뒤** 텍스트만. 도구 호출이 오면 그때까지 `answer` 에 모인 글(도구 사이 메모 —
    예 「Two readings: concern and site. Query concern.」)을 `interim_text[]` 로 옮기고 `answer` 를 비운다 ·
    `progress.emit("interim", text=…)` 로 화면에도 「답 칸 글을 메모로」 알린다. 시간: `Timer.mark_text` 가 첫 글자(메모 포함
    — `ttft_ms`) · `Timer.new_segment` 가 첫 답 글자(`first_answer_ms`)를 도구 호출 때마다 다시 잰다.
    """

    def __init__(self, res: dict, timer: Timer, mcp: McpAttach | None) -> None:
        self.res, self.timer, self.mcp = res, timer, mcp
        self.tools: dict[str, tuple[dict, dict]] = {}
        self.round: dict | None = None
        self.n_round = 0
        self.usage: dict[str, dict] = {}  # LLM 왕복(message id)마다 usage — message_start(입력) · message_delta(출력 끝 값)
        self.cur_msg: str | None = None

    def line(self, raw: str) -> None:
        try:
            ev = json.loads(raw)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            self._init(ev)
        elif t == "system" and ev.get("subtype") == "thinking_tokens":
            self.res["thinking_seen"] = True
        elif t == "stream_event":
            self._stream(ev.get("event") or {})
        elif t == "assistant":
            self._assistant(ev)
        elif t == "user":
            self._user(ev)
        elif t == "rate_limit_event":
            info = ev.get("rate_limit_info") or ev
            if str(info.get("status", "")).lower() in ("rejected", "blocked"):
                self.res["error"] = f"한도: {info.get('rateLimitType')}"
        elif t == "result":
            self._result(ev)

    def _init(self, ev: dict) -> None:
        self.res["observed_model"] = ev.get("model")
        self.res["cli"].update({"cli_version": ev.get("claude_code_version"), "api_key_source": ev.get("apiKeySource"),
                                "session_tools": ev.get("tools"),
                                "mcp_servers": ev.get("mcp_servers")})
        src = ev.get("apiKeySource")
        if src not in (None, "none"):
            self.res["error"] = f"구독 아님: apiKeySource={src}"
            raise StopRun
        if self.mcp:
            st = {s.get("name"): s.get("status") for s in ev.get("mcp_servers") or []}
            if st.get(self.mcp.name) != "connected":
                self.res["error"] = f"MCP 연결 안 됨: {self.mcp.name}={st.get(self.mcp.name)}"
                raise StopRun

    def _open_round(self) -> None:
        if self.round is None:
            self.n_round += 1
            self.round = self.timer.open(f"llm#{self.n_round}")

    def _close_round(self) -> None:
        if self.round is not None:
            self.timer.close(self.round)
            self.res["llm_round_trips"] += 1
            self.round = None

    def _stream(self, e: dict) -> None:
        et = e.get("type")
        if et == "message_start":
            self._open_round()
            msg = e.get("message") or {}
            self.cur_msg = str(msg.get("id") or f"#{len(self.usage) + 1}")
            self._usage(self.cur_msg, msg.get("usage"))
        elif et == "content_block_start":
            cb = e.get("content_block") or {}
            if cb.get("type") in ("thinking", "redacted_thinking"):
                self.res["thinking_seen"] = True
            elif cb.get("type") == "tool_use" and cb.get("name") != STRUCTURED_TOOL:
                self._split()
        elif et == "content_block_delta":
            d = e.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                self.timer.mark_text()
                self.res["answer"] += d["text"]
                progress.emit("delta", text=d["text"])
            elif d.get("type") in ("thinking_delta", "signature_delta"):
                self.res["thinking_seen"] = True
        elif et == "message_delta":
            if self.cur_msg is not None:
                self._usage(self.cur_msg, e.get("usage"))
            if (e.get("delta") or {}).get("stop_reason"):
                self.res["stop_reason"] = e["delta"]["stop_reason"]
        elif et == "message_stop":
            self._close_round()

    def _usage(self, mid: str, u: Any) -> None:
        if isinstance(u, dict):
            d = self.usage.setdefault(mid, {})
            d.update({k: u[k] for k in USAGE_KEYS if isinstance(u.get(k), int | float)})

    def _assistant(self, ev: dict) -> None:
        msg = ev.get("message") or {}
        if msg.get("id") and msg["id"] not in self.usage:  # 부분 메시지 흐름이 없을 때(같은 id 는 한 번)
            self._usage(str(msg["id"]), msg.get("usage"))
        for b in (ev.get("message") or {}).get("content") or []:
            if b.get("type") in ("thinking", "redacted_thinking"):
                self.res["thinking_seen"] = True
            if b.get("type") != "tool_use":
                continue
            name = str(b.get("name") or "")
            if name == STRUCTURED_TOOL:
                if isinstance(b.get("input"), dict):
                    self.res["structured"] = b["input"]
                continue
            self._split()
            self._close_round()
            short = name.split("__", 2)[-1] if name.startswith(MCP_PREFIX) else name
            st = self.timer.open(f"tool:{short}#{len(self.res['tool_calls']) + 1}")
            call = {"tool": short, "full_name": name, "input": b.get("input"), "output_bytes": None,
                    "output_text": None, "is_error": None, "cli_truncated": False}
            self.tools[str(b.get("id"))] = (st, call)
            self.res["tool_calls"].append(call)

    def _split(self) -> None:
        """도구 호출 앞 글 → `interim_text[]` · 답은 다음 글부터(한 도구 호출에 두 번 와도 두 번째는 할 일 없음)."""
        self.timer.new_segment()
        text = self.res["answer"]
        if not text:
            return
        self.res["interim_text"].append(text)
        self.res["answer"] = ""
        progress.emit("interim", text=text)

    def _user(self, ev: dict) -> None:
        for b in (ev.get("message") or {}).get("content") or []:
            if b.get("type") != "tool_result":
                continue
            hit = self.tools.pop(str(b.get("tool_use_id")), None)
            if not hit:
                continue
            st, call = hit
            self.timer.close(st)
            text = _result_text(b.get("content"))
            call.update({"output_bytes": len(text.encode("utf-8")), "output_text": text,
                         "is_error": bool(b.get("is_error")),
                         "cli_truncated": any(m in text for m in CLI_TRUNC_MARKERS)})

    def _result(self, ev: dict) -> None:
        self._close_round()
        u = ev.get("usage") or {}
        rounds = [d for d in self.usage.values() if d]
        self.res["token_rounds"] = rounds
        if rounds:  # 도구 왕복 합(코디 발견 — C 토큰 0) · 없으면 result 의 usage
            self.res["tokens"] = {k: sum(int(d.get(k) or 0) for d in rounds) for k in USAGE_KEYS
                                  if any(k in d for d in rounds)}
        else:
            self.res["tokens"] = {k: u.get(k) for k in USAGE_KEYS if k in u}
        self.res["tokens_result"] = {k: u.get(k) for k in USAGE_KEYS if k in u}  # 대조용(CLI 가 낸 값 그대로)
        mu = ev.get("modelUsage") or {}
        self.res["observed_models"] = list(mu) or None
        self.res["cli"].update({"num_turns": ev.get("num_turns"), "duration_ms": ev.get("duration_ms"),
                                "subtype": ev.get("subtype")})
        if isinstance(ev.get("structured_output"), dict):
            self.res["structured"] = self.res["structured"] or ev["structured_output"]
        if (not self.res["answer"] and isinstance(ev.get("result"), str)
                and ev["result"] not in self.res["interim_text"]):  # 메모를 답으로 되살리지 않는다
            self.res["answer"] = ev["result"]
        if ev.get("is_error") or str(ev.get("subtype", "")).startswith("error"):
            msg = ev.get("result") if isinstance(ev.get("result"), str) else ev.get("subtype")
            self.res["error"] = self.res["error"] or f"Claude CLI: {str(msg)[:300]}"


_SEMS: dict[tuple[int, int], asyncio.Semaphore] = {}


def _semaphore(n: int) -> asyncio.Semaphore:
    key = (id(asyncio.get_running_loop()), n)
    if key not in _SEMS:
        _SEMS[key] = asyncio.Semaphore(max(1, n))
    return _SEMS[key]


async def run(spec: CliSpec, *, system: str, prompt: str, mcp: McpAttach | None = None,
              schema: dict | None = None, timer: Timer | None = None, max_concurrent: int = 1) -> dict:
    """CLI 한 번. 오류는 `error` 칸에(예외를 올리지 않는다 — 그 열만 오류)."""
    timer = timer or Timer()
    res = new_result(spec)
    a = await auth.check(spec.bin)
    res["cli"].update({"auth_kind": a.kind, "auth_detail": a.detail, "cli_version": a.version})
    if not a.ok:
        res["error"] = f"인증: {a.detail}"
        timer.finish()
        res["timing"] = timer.as_dict()
        return res
    async with _semaphore(max_concurrent):
        work = Path(tempfile.mkdtemp(prefix="clinic-chat-cli-"))
        t = time.perf_counter()
        try:
            ing = ClaudeIngest(res, timer, mcp)
            proc = await run_cli(claude_args(spec, system, mcp, schema), cwd=str(work), env=cli_env(spec),
                                 timeout_s=spec.timeout_s, on_line=ing.line, stdin_text=prompt)
            res["cli"].update({"returncode": proc.returncode,
                               "spawn_to_exit_ms": round((time.perf_counter() - t) * 1000, 1)})
            if proc.timed_out:
                res["error"] = res["error"] or f"시간 초과({spec.timeout_s:g}초)"
            elif proc.returncode not in (0, None) and not res["error"]:
                res["error"] = f"claude 종료 코드 {proc.returncode}: {' | '.join(proc.stderr_tail)[:300]}"
        except Exception as e:  # noqa: BLE001 — 그 열만 오류
            res["error"] = res["error"] or f"{type(e).__name__}: {e}"[:300]
        finally:
            timer.finish()
            res["timing"] = timer.as_dict()
            shutil.rmtree(work, ignore_errors=True)
    return res


async def _ping(spec: CliSpec) -> dict:
    """E2 · E3 — 회사 데이터 없는 빈 프롬프트 한 번."""
    r = await run(spec, system="Answer in one short sentence.", prompt="Say hello in one short sentence.")
    return {"ok": not r["error"], "error": r["error"], "requested_model": spec.model,
            "observed_model": r["observed_model"], "observed_models": r["observed_models"],
            "reasoning": spec.reasoning, "effort_arg": spec.effort_arg(), "thinking_seen": r["thinking_seen"],
            "billed": r["cost"]["billed"], "auth": r["cli"].get("auth_kind"), "cli_version": r["cli"].get("cli_version"),
            "total_ms": r["timing"].get("total_ms"), "ttft_ms": r["timing"].get("ttft_ms"),
            "answer_chars": len(r["answer"]), "tokens": r["tokens"]}


def main() -> None:
    from clinic_chat.config import load_settings

    ap = argparse.ArgumentParser(prog="clinic_chat.llm.cli")
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("--reasoning", choices=["off", "lowest_effort", "on"], default="off")
    args = ap.parse_args()
    if not args.ping:
        ap.print_help()
        return
    s = load_settings()
    spec = CliSpec(s.claude_cli_bin, args.model or s.answer_model, s.claude_cli_effort, s.cli_timeout_sec,
                   reasoning=args.reasoning, lowest_effort=s.lowest_effort)
    out = asyncio.run(_ping(spec))
    print(json.dumps(out, ensure_ascii=False))
    if not out["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
