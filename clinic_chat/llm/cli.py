"""CLI provider — `claude -p` stream-json · `codex exec --json` 를 띄워 이벤트를 CaseTimer · 기록 꼴로 옮긴다.

근거: cli-research §1(명령 꼴) · §2(측정) · §5(격리) · §6(구현). 참고 설계(읽기만) Strong_hajin platform/claude_cli.py
`ClaudeEventIngest` · codex_cli.py `CodexEventIngest` · `prepare_isolated_codex_home`.

- 격리: Claude = `--setting-sources ""` · `--tools ""`(MCP 도구만 남음) · `--strict-mcp-config` · `--disable-slash-commands` ·
  `--no-session-persistence` · `--system-prompt`(Claude Code 기본 프롬프트 갈아 끼움) · 임시 cwd.
  Codex = 전용 CODEX_HOME(auth.json 링크만) · `--ignore-user-config` · `--ignore-rules` · `--ephemeral` · read-only ·
  셸 · exec · 웹검색 · 이미지 · 플러그인 · 스킬 끔 · `model_instructions_file` · 임시 cwd.
- 자식 env 는 cli_process.child_env() — API 키 지움. 실행 앞에 auth.check() 로 구독인지 봄(아니면 그 열 오류).
- 도구 루프 상한 없음(CLI 칸 없음 · D-3) — 타임아웃 · Claude `--max-budget-usd` · 도구 호출 수 기록.
- ttft: Claude = 첫 text_delta(토큰) · Codex = 첫 agent_message 완료(메시지) → `ttft_kind`.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clinic_chat.cases.loop import mark_call, mark_result, node_ids, summarize_calls
from clinic_chat.config import ROOT
from clinic_chat.llm import auth
from clinic_chat.llm.cli_process import StopRun, child_env, run_cli
from clinic_chat.timing import CaseTimer

MCP_PREFIX = "mcp__"
STRUCTURED_TOOL = "StructuredOutput"
# 내장 도구 끔(W-6 — rerun-v3 의 bash 10건 · `--tools ""` 와 같이)
DISALLOWED_TOOLS = "Bash,Read,Grep,Glob,Edit,Write"
RATE_LIMIT_TEXT = re.compile(r"usage limit|rate limit|rate_limit|too many requests|quota", re.IGNORECASE)
CACHE_DIR = ROOT / ".cache"  # .gitignore
CODEX_AUTH = Path.home() / ".codex" / "auth.json"  # 사용자 Codex 로그인(링크만 — 내용은 읽지 않음)


@dataclass
class CliSpec:
    provider: str  # claude-cli | codex-cli
    bin: str
    model: str
    effort: str
    timeout_s: float
    max_budget_usd: float | None = None  # Claude 만
    service_tier: str | None = None  # Codex 만
    result_max: int | None = None  # 도구 결과 한도(§3-5) — Claude env MAX_MCP_OUTPUT_TOKENS · Codex 토큰 한도


@dataclass
class McpAttach:
    name: str  # 서버 id(도구 이름 접두 mcp__<name>__)
    url: str | None = None  # Streamable HTTP(허브)
    command: str | None = None  # stdio(채점용 로컬 원장 MCP)
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    intent_tools: frozenset[str] = frozenset()  # 스키마에 intent 가 있는 도구(기록 intent_passed — §3-6)
    tools: tuple[str, ...] = ()  # 서버 도구 이름(Codex 도구별 output_token_limit)


def new_result(spec: CliSpec) -> dict:
    return {"answer": "", "stop_reason": None, "loop_capped": False, "error": None, "refusal": False,
            "timing": {}, "tokens": {}, "trace": {"tool_calls": [], "cli": {"provider": spec.provider}},
            "cost": {"usd": 0.0, "by_stage": {}, "billed": "subscription"}, "structured": None}


# ───────────── Claude ─────────────

def claude_args(spec: CliSpec, system: str, mcp: McpAttach | None, schema: dict | None) -> list[str]:
    """프롬프트는 stdin 으로(`--allowedTools` · `--tools` 가 가변 인자라 뒤 위치 인자를 삼킴 — 10-30 실물 확인)."""
    a = [spec.bin, "-p", "--model", spec.model, "--effort", spec.effort, "--system-prompt", system,
         "--setting-sources", "", "--tools", "", "--disallowedTools", DISALLOWED_TOOLS,
         "--disable-slash-commands", "--strict-mcp-config",
         "--no-session-persistence", "--permission-mode", "bypassPermissions",
         "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    if spec.max_budget_usd:
        a += ["--max-budget-usd", f"{spec.max_budget_usd:g}"]
    if mcp:
        server: dict[str, Any] = {"type": "http", "url": mcp.url} if mcp.url else \
            {"type": "stdio", "command": mcp.command, "args": mcp.args, "env": mcp.env}
        a += ["--mcp-config", json.dumps({"mcpServers": {mcp.name: server}}), "--allowedTools", f"{MCP_PREFIX}{mcp.name}"]
    if schema:
        a += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    return a


class ClaudeIngest:
    """`claude -p --output-format stream-json --verbose --include-partial-messages` 줄 → timer · 결과."""

    def __init__(self, res: dict, timer: CaseTimer, on_text: Callable[[str], None], boot: dict,
                 collect_node_ids: bool, mcp: McpAttach | None) -> None:
        self.res, self.timer, self.on_text, self.boot = res, timer, on_text, boot
        self.collect, self.mcp = collect_node_ids, mcp
        self.cli = res["trace"]["cli"]
        self.round: dict | None = None
        self.last_round: dict | None = None
        self.n_round = 0
        self.n_tool = 0
        self.round_first: float | None = None
        self.th_start: float | None = None
        self.th_end: float | None = None
        self.tools: dict[str, tuple[dict, dict]] = {}  # tool_use id → (stage, call)
        self.cli.update({"thinking_seen": False, "rate_limits": [], "api_retries": [], "events": 0})

    def line(self, raw: str) -> None:
        try:
            ev = json.loads(raw)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        self.cli["events"] += 1
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            self._init(ev)
        elif t == "system" and ev.get("subtype") == "status" and ev.get("status") == "requesting":
            self._round_open()
        elif t == "system" and ev.get("subtype") == "thinking_tokens":
            self._thinking()
        elif t == "system" and ev.get("subtype") == "api_retry":
            self.cli["api_retries"].append({k: ev.get(k) for k in ("error", "attempt", "retry_delay_ms") if k in ev})
        elif t == "stream_event":
            self._stream(ev.get("event") or {})
        elif t == "assistant":
            self._assistant(ev)
        elif t == "user":
            self._user(ev)
        elif t == "rate_limit_event":
            info = ev.get("rate_limit_info") or ev
            row = {k: info.get(k) for k in ("status", "rateLimitType", "utilization", "resetsAt") if k in info}
            win = (info.get("unifiedWindows") or {})  # 10-30 실물: 창마다 utilization · resetsAt
            if win:
                row["windows"] = {k: {x: v.get(x) for x in ("utilization", "resetsAt")} for k, v in win.items()}
                row.setdefault("utilization", (win.get(info.get("rateLimitType")) or {}).get("utilization"))
            self.cli["rate_limits"].append(row)
            if str(info.get("status", "")).lower() in ("rejected", "blocked"):
                self.res["error"] = f"한도: {info.get('rateLimitType')} · 다시 열림 {info.get('resetsAt')}"
                self.cli["rate_limited"] = True
        elif t == "result":
            self._result(ev)

    def _init(self, ev: dict) -> None:
        self.timer.close_stage(self.boot)
        self.cli.update({"session_tools": ev.get("tools"), "mcp_servers": ev.get("mcp_servers"),
                         "skills": ev.get("skills"), "slash_commands": ev.get("slash_commands"),
                         "plugins": [p.get("name") for p in ev.get("plugins") or []],
                         "memory_paths": bool(ev.get("memory_paths")),
                         "observed_model": ev.get("model"), "api_key_source": ev.get("apiKeySource"),
                         "cli_version": ev.get("claude_code_version"), "permission_mode": ev.get("permissionMode")})
        src = ev.get("apiKeySource")
        if src not in (None, "none"):  # 키로 돌고 있음 — 조용히 API 과금 금지
            self.res["error"] = f"구독 아님: apiKeySource={src}"
            raise StopRun
        if self.mcp:
            st = {s.get("name"): s.get("status") for s in ev.get("mcp_servers") or []}
            if st.get(self.mcp.name) != "connected":
                self.res["error"] = f"MCP 연결 안 됨: {self.mcp.name}={st.get(self.mcp.name)}"
                raise StopRun

    # 바퀴 시간(10-30 실물 순서): status:requesting → message_start → [thinking] → tool_use 블록 · assistant(tool_use)
    # → message_stop → user(tool_result) → status:requesting … / 마지막 바퀴는 text Δ … message_stop → result.
    # claude#N = 요청 시작 ~ 그 바퀴 마지막 이벤트(도구 호출 시작 또는 답 끝) · thinking_ms 는 따로 칸.
    def _round_open(self) -> None:
        if self.boot.get("end_ms") is None:
            self.timer.close_stage(self.boot)
        if self.round is not None:
            self._round_close()
        self.n_round += 1
        self.round = self.timer.open_stage(f"claude#{self.n_round}")
        self.round_first = None
        self.th_start = self.th_end = None

    def _thinking(self) -> None:
        self.cli["thinking_seen"] = True
        if self.round is None:
            return
        now = self.timer.clock.ms()
        if self.th_start is None:
            self.th_start = now
            self.round["thinking"] = True
            self.timer.touch(self.round)  # 화면: 「생각 중」
        self.th_end = now

    def _output(self) -> None:
        """바퀴의 첫 출력(글 · 도구 호출) — 생각 끝."""
        if self.round is not None and self.th_start is not None and self.round.get("thinking_ms") is None:
            self.round["thinking_ms"] = round(self.timer.clock.ms() - self.th_start, 1)

    def _round_close(self, at: float | None = None) -> None:
        st = self.round
        if st is None:
            return
        if self.th_start is not None and st.get("thinking_ms") is None:
            st["thinking_ms"] = round((self.th_end or self.timer.clock.ms()) - self.th_start, 1)
        self.timer.close_stage(st, at)
        self.timer.llm_round_trips += 1
        self.timer.ttft_answer_ms = self.round_first
        self.last_round = st
        self.round = None

    def _stream(self, e: dict) -> None:
        et = e.get("type")
        if et == "message_start":
            if self.round is None:  # status:requesting 가 없는 판 — 여기서 엶
                self._round_open()
            u = (e.get("message") or {}).get("usage")
            if u:
                self.round["tokens"] = _claude_usage(u)
        elif et == "content_block_start":
            kind = (e.get("content_block") or {}).get("type")
            if kind in ("thinking", "redacted_thinking"):
                self._thinking()
            elif kind in ("tool_use", "text"):
                self._output()
        elif et == "content_block_delta":
            d = e.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                self._output()
                ms = self.timer.mark_text()
                if self.round_first is None:
                    self.round_first = ms
                self.res["answer"] += d["text"]
                self.on_text(d["text"])
            elif d.get("type") in ("thinking_delta", "signature_delta"):
                self._thinking()
        elif et == "message_delta":
            tgt = self.round or self.last_round
            if tgt is not None and e.get("usage"):
                tgt["tokens"] = {**(tgt.get("tokens") or {}), **_claude_usage(e["usage"])}
            if (e.get("delta") or {}).get("stop_reason"):
                self.res["stop_reason"] = e["delta"]["stop_reason"]
        elif et == "message_stop" and self.round is not None:
            self._round_close()  # 답 끝(도구가 없던 바퀴)

    def _assistant(self, ev: dict) -> None:
        for b in (ev.get("message") or {}).get("content") or []:
            if b.get("type") == "thinking":
                self.cli["thinking_seen"] = True
            if b.get("type") != "tool_use":
                continue
            name = str(b.get("name") or "")
            if name == STRUCTURED_TOOL:
                if isinstance(b.get("input"), dict):
                    self.res["structured"] = b["input"]
                continue
            short = name.split("__", 2)[-1] if name.startswith(MCP_PREFIX) else name
            now = self.timer.clock.ms()
            if self.round is not None:  # 바퀴 끝 = 도구 호출 시작(같은 시각)
                self._round_close(now)
            self.n_tool += 1
            st = self.timer.open_stage(f"tool:{short}#{self.n_tool}", at=now)
            mine = bool(self.mcp) and name.startswith(f"{MCP_PREFIX}{self.mcp.name}__")
            call = mark_call({"tool": short, "input": b.get("input"), "output_bytes": None, "ms": None, "is_error": None},
                             intent_tools=self.mcp.intent_tools if self.mcp else (), stray=not mine, name=name)
            if not mine:  # S-2 확인용 — CLI 원문 tool_use 블록 그대로(입력 꼴)
                self.cli.setdefault("stray_events", []).append(b)
            self.tools[str(b.get("id"))] = (st, call)
            self.res["trace"]["tool_calls"].append(call)

    def _user(self, ev: dict) -> None:
        for b in (ev.get("message") or {}).get("content") or []:
            if b.get("type") != "tool_result":
                continue
            hit = self.tools.pop(str(b.get("tool_use_id")), None)
            if not hit:
                continue  # StructuredOutput 확인 등
            st, call = hit
            self.timer.close_stage(st)
            text = _result_text(b.get("content"))
            mark_result(call, text, "claude-cli")
            call.update({"ms": round(st["end_ms"] - st["start_ms"], 1), "is_error": bool(b.get("is_error"))})
            if self.collect and text:
                try:
                    call["node_ids"] = node_ids(json.loads(text))
                except ValueError:
                    call["node_ids"] = []

    def _result(self, ev: dict) -> None:
        if self.round is not None:
            self._round_close()
        u = _claude_usage(ev.get("usage") or {})
        self.res["tokens"] = {"total": u, "by_model": {m: _claude_usage(v) for m, v in (ev.get("modelUsage") or {}).items()}}
        self.cli.update({"num_turns": ev.get("num_turns"), "duration_ms": ev.get("duration_ms"),
                         "duration_api_ms": ev.get("duration_api_ms"), "subtype": ev.get("subtype"),
                         "cli_ttft_ms": ev.get("ttft_ms"), "cli_time_to_request_ms": ev.get("time_to_request_ms"),
                         "observed_models": list((ev.get("modelUsage") or {}).keys()) or None})
        est = ev.get("total_cost_usd")
        if isinstance(est, (int, float)):
            self.res["cost"].update({"usd": round(float(est), 6), "usd_estimate": round(float(est), 6),
                                     "estimate_note": "CLI 추정(구독 — 실제 청구 아님)"})
        if isinstance(ev.get("structured_output"), dict):
            self.res["structured"] = self.res["structured"] or ev["structured_output"]
        if not self.res["answer"] and isinstance(ev.get("result"), str):
            self.res["answer"] = ev["result"]
        if ev.get("is_error") or str(ev.get("subtype", "")).startswith("error"):
            msg = ev.get("result") if isinstance(ev.get("result"), str) else ev.get("subtype")
            self.res["error"] = self.res["error"] or f"Claude CLI: {str(msg)[:300]}"
        if self.res.get("stop_reason") == "refusal":
            self.res["refusal"] = True


_CAMEL = {"inputTokens": "input_tokens", "outputTokens": "output_tokens",
          "cacheCreationInputTokens": "cache_creation_input_tokens", "cacheReadInputTokens": "cache_read_input_tokens",
          "thinkingTokens": "thinking_tokens", "costUSD": "cost_usd"}


def _claude_usage(u: dict) -> dict:
    """usage(snake) · modelUsage(camel — 10-30 실물: inputTokens · costUSD · thinkingTokens) 둘 다."""
    u = {_CAMEL.get(k, k): v for k, v in (u or {}).items()}
    return {k: u.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                       "cache_read_input_tokens", "thinking_tokens", "cost_usd") if k in u}


def _result_text(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return json.dumps(content, ensure_ascii=False)


# ───────────── Codex ─────────────

def prepare_codex_home(home: Path, auth_file: Path) -> Path:
    """전용 CODEX_HOME — auth.json 링크만(config.toml · AGENTS.md · 스킬 있으면 거부). 참고 설계 그대로."""
    home = home.resolve()
    if not auth_file.is_file():
        raise RuntimeError("Codex 인증 파일 없음")
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    for bad in ("AGENTS.md", "config.toml"):
        if (home / bad).exists():
            raise RuntimeError(f"격리 CODEX_HOME 에 {bad} 가 있음")
    skills = home / "skills"
    if skills.exists() and [p for p in skills.iterdir() if p.name != ".system"]:
        raise RuntimeError("격리 CODEX_HOME 에 스킬이 있음")
    link = home / "auth.json"
    if link.exists() or link.is_symlink():
        if link.resolve() != auth_file.resolve():
            raise RuntimeError("격리 CODEX_HOME 의 auth.json 이 다름")
    else:
        link.symlink_to(auth_file)
    return home


def codex_args(spec: CliSpec, instructions_file: Path, cwd: str, prompt: str, mcp: McpAttach | None,
               schema_file: Path | None, out_file: Path | None) -> list[str]:
    a = [spec.bin, "exec", "--json", "--color", "never", "--ephemeral", "--skip-git-repo-check", "-C", cwd,
         "-s", "read-only", "--ignore-user-config", "--ignore-rules", "-m", spec.model,
         "-c", f'model_reasoning_effort="{spec.effort}"',
         "-c", f"model_instructions_file={json.dumps(str(instructions_file))}", "-c", "project_doc_max_bytes=0",
         "-c", "features.shell_tool=false", "-c", "features.unified_exec=false", "-c", 'web_search="disabled"',
         "-c", "features.plugins=false", "-c", "features.skill_search=false",
         "-c", "features.skip_host_skill_discovery=true", "-c", "suppress_unstable_features_warning=true",
         "-c", 'shell_environment_policy.inherit="none"']
    # tools.view_image 는 0.166 에서 「알 수 없는 설정」 경고(10-30 실물) — 빼고 실물 JSONL 의 item 종류로 확인
    if spec.service_tier:
        a += ["-c", f'service_tier="{spec.service_tier}"']
    if spec.result_max:  # 도구 결과 토큰 한도(§3-5) — 전체 + 도구별
        a += ["-c", f"tool_output_token_limit={spec.result_max}"]
        for t in (mcp.tools if mcp else ()):
            a += ["-c", f"mcp_servers.{mcp.name}.tools.{t}.output_token_limit={spec.result_max}"]
    if mcp and mcp.url:
        a += ["-c", f"mcp_servers.{mcp.name}.url={json.dumps(mcp.url)}", "-c", f"mcp_servers.{mcp.name}.required=true",
              "-c", f'mcp_servers.{mcp.name}.default_tools_approval_mode="approve"',
              "-c", f"mcp_servers.{mcp.name}.tool_timeout_sec=60"]
    if schema_file and out_file:
        a += ["--output-schema", str(schema_file), "-o", str(out_file)]
    return [*a, prompt]


class CodexIngest:
    """`codex exec --json` 줄 → timer · 결과. 글자 델타가 없어 ttft 는 첫 agent_message 완료(메시지 단위)."""

    def __init__(self, res: dict, timer: CaseTimer, on_text: Callable[[str], None], boot: dict,
                 collect_node_ids: bool, mcp: McpAttach | None = None) -> None:
        self.res, self.timer, self.on_text, self.boot, self.collect = res, timer, on_text, boot, collect_node_ids
        self.mcp = mcp
        self.cli = res["trace"]["cli"]
        self.turn: dict | None = None
        self.n_seg = 0
        self.n_tool = 0
        self.tools: dict[str, tuple[dict, dict]] = {}
        self.cli.update({"thinking_seen": False, "item_types": [], "events": 0, "messages": 0, "warnings": []})
        self.completed = False

    def line(self, raw: str) -> None:
        try:
            ev = json.loads(raw)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        self.cli["events"] += 1
        t = str(ev.get("type", ""))
        if isinstance(ev.get("model"), str):
            self.cli["observed_model"] = ev["model"]
        if t == "thread.started":
            self.cli["thread_started_ms"] = self.timer.clock.ms()
        elif t == "turn.started":
            self._seg_open(at=self.timer.close_stage(self.boot))
        elif t in ("turn.completed", "turn.failed"):
            if isinstance(ev.get("usage"), dict):
                u = ev["usage"]
                self.res["tokens"] = {"total": {"input_tokens": u.get("input_tokens", 0),
                                                "cache_read_input_tokens": u.get("cached_input_tokens", 0),
                                                "output_tokens": u.get("output_tokens", 0),
                                                "reasoning_output_tokens": u.get("reasoning_output_tokens", 0)}}
            self._seg_close()
            if t == "turn.failed":
                self._fail(ev.get("error") or ev)
            else:
                self.completed = True
                self.res["stop_reason"] = "end_turn"
        elif t == "error":  # 최상위 error — 턴이 끝나지 않으면 finish() 가 오류로 올림
            self._warn(ev.get("message") or ev.get("error") or ev)
        elif t.startswith("item."):
            self._item(t.split(".", 1)[1], ev.get("item") or {})

    def _warn(self, err: Any) -> None:
        msg = str(err.get("message") if isinstance(err, dict) else err)[:300]
        self.cli["warnings"].append(msg)
        if RATE_LIMIT_TEXT.search(msg):
            self.cli["rate_limited"] = True

    def finish(self) -> None:
        """턴이 끝나지 않았으면 마지막 경고를 오류로(경고 item 은 실행을 멈추지 않음 — 10-30 실물)."""
        if not self.completed and not self.res["error"] and self.cli["warnings"]:
            self.res["error"] = f"Codex CLI: {self.cli['warnings'][-1]}"

    def _fail(self, err: Any) -> None:
        msg = err.get("message") if isinstance(err, dict) else str(err)
        msg = str(msg)[:300]
        if RATE_LIMIT_TEXT.search(msg):
            self.cli["rate_limited"] = True
        self.res["error"] = self.res["error"] or f"Codex CLI: {msg}"

    # 모델 구간 — 턴을 도구 호출로 나눔(codex#1 → tool → codex#2 …): 단계가 겹치지 않고 합이 맞게(10-30 피드백)
    def _seg_open(self, at: float | None = None) -> None:
        self.n_seg += 1
        self.turn = self.timer.open_stage(f"codex#{self.n_seg}", at=at)

    def _seg_close(self, at: float | None = None) -> None:
        if self.turn is not None:
            self.timer.close_stage(self.turn, at)
            self.timer.llm_round_trips += 1
            self.turn = None

    def _item(self, phase: str, item: dict) -> None:
        it = str(item.get("type", ""))
        if it and it not in self.cli["item_types"]:
            self.cli["item_types"].append(it)
        iid = str(item.get("id") or f"item-{self.n_tool}")
        if it == "reasoning":
            self.cli["thinking_seen"] = True
            if self.turn is not None and phase == "completed" and self.turn.get("thinking_ms") is None:
                self.turn["thinking"] = True  # reasoning item 이 끝난 때까지 = 생각(어림)
                self.turn["thinking_ms"] = round(self.timer.clock.ms() - self.turn["start_ms"], 1)
                self.timer.touch(self.turn)
        elif it == "agent_message" and phase == "completed" and isinstance(item.get("text"), str):
            ms = self.timer.mark_text()  # 메시지 단위
            self.timer.ttft_answer_ms = ms
            text = item["text"] if not self.res["answer"] else "\n\n" + item["text"]
            self.res["answer"] += text
            self.cli["messages"] += 1
            self.on_text(text)
        elif it == "error":  # 설정 경고 등 — 턴은 계속(10-30 실물: 「unrecognized configuration」 · 「Under-development」)
            if phase == "completed":
                self._warn(item.get("message"))
        elif it == "mcp_tool_call" or "tool" in it or it in ("command_execution", "web_search", "file_change"):
            if iid not in self.tools:
                now = self.timer.clock.ms()
                self._seg_close(now)  # 모델 구간 끝 = 도구 호출 시작(같은 시각)
                self.n_tool += 1
                name = str(item.get("tool") or item.get("name") or it)
                st = self.timer.open_stage(f"tool:{name}#{self.n_tool}", at=now)
                mine = it == "mcp_tool_call" and bool(self.mcp) and item.get("server") in (None, self.mcp.name)
                # stray 나눔용 이름 — 내장 항목(명령 · 검색 · 파일)과 다른 서버 도구는 foreign 으로 읽히게
                raw_name = name if it != "mcp_tool_call" or item.get("server") in (None, getattr(self.mcp, "name", None)) \
                    else f"mcp__{item.get('server')}__{name}"
                if it != "mcp_tool_call":
                    raw_name = f"{it}:{name}"
                call = mark_call({"tool": name, "input": item.get("arguments"), "output_bytes": None, "ms": None,
                                  "is_error": None, "item_type": it},
                                 intent_tools=self.mcp.intent_tools if self.mcp else (), stray=not mine,
                                 name=raw_name)
                if not mine:
                    self.cli.setdefault("stray_events", []).append(item)
                self.tools[iid] = (st, call)
                self.res["trace"]["tool_calls"].append(call)
            st, call = self.tools[iid]
            if phase == "completed" and st.get("end_ms") is None:
                end = self.timer.close_stage(st)
                if self.turn is None and not self.completed:
                    self._seg_open(at=end)  # 도구 결과 뒤 다음 모델 구간(같은 시각)
                result = item.get("result")
                # 10-30 실물 꼴: result = {content: [{type: text, text}], structured_content} — 모델이 받은 글 = content
                if isinstance(result, dict) and isinstance(result.get("content"), list):
                    text = _result_text(result["content"])
                    data = result.get("structured_content")
                    if data is None and text:
                        try:
                            data = json.loads(text)
                        except ValueError:
                            data = None
                else:
                    text = json.dumps(result, ensure_ascii=False) if result is not None else None
                    data = result
                status = str(item.get("status", ""))
                mark_result(call, text or None, "codex-cli")
                call.update({"ms": round(st["end_ms"] - st["start_ms"], 1),
                             "is_error": status in ("failed", "error", "declined") or bool(item.get("error"))})
                if self.collect and data is not None:
                    call["node_ids"] = node_ids(data)


# ───────────── 실행 ─────────────

async def run(spec: CliSpec, *, system: str, prompt: str, timer: CaseTimer, on_text: Callable[[str], None],
              mcp: McpAttach | None = None, schema: dict | None = None, collect_node_ids: bool = False) -> dict:
    """CLI 한 번 — 결과는 case 결과 꼴(answer · timing · tokens · trace · cost) + structured."""
    res = new_result(spec)
    cli = res["trace"]["cli"]
    cli.update({"model": spec.model, "effort": spec.effort, "service_tier": spec.service_tier,
                "max_budget_usd": spec.max_budget_usd, "tool_loop_max": None, "ttft_kind":
                "token" if spec.provider == "claude-cli" else "message"})
    a = await auth.check(spec.provider, spec.bin)
    cli.update({"auth_kind": a.kind, "auth_detail": a.detail, "cli_version": a.version})
    if not a.ok:
        res["error"] = f"인증: {a.detail}"
        timer.finish()
        res["timing"] = _timing(timer, spec)
        return res
    CACHE_DIR.mkdir(exist_ok=True)
    sem = _semaphore()
    if sem.locked():  # 동시 CLI 상한(설정 CLI_MAX_CONCURRENT) — 기다린 시간은 cli_queue 단계로
        q = timer.open_stage("cli_queue")
        await sem.acquire()
        timer.close_stage(q)
    else:
        await sem.acquire()
    try:
        return await _run_locked(spec, res, cli, system=system, prompt=prompt, timer=timer, on_text=on_text, mcp=mcp,
                                 schema=schema, collect_node_ids=collect_node_ids)
    finally:
        sem.release()


_SEMS: dict[int, Any] = {}
MAX_CONCURRENT = 6  # server 가 설정(CLI_MAX_CONCURRENT)으로 바꿈


def _semaphore():
    import asyncio

    loop = asyncio.get_running_loop()
    key = id(loop)
    if key not in _SEMS or _SEMS[key][0] != MAX_CONCURRENT:
        _SEMS[key] = (MAX_CONCURRENT, asyncio.Semaphore(MAX_CONCURRENT))
    return _SEMS[key][1]


async def _run_locked(spec: CliSpec, res: dict, cli: dict, *, system: str, prompt: str, timer: CaseTimer,
                      on_text: Callable[[str], None], mcp: McpAttach | None, schema: dict | None,
                      collect_node_ids: bool) -> dict:
    # 임시 cwd 는 레포 밖(시스템 임시 폴더) — git 상태 · 프로젝트 자동 메모리 경로가 레포에 묶이지 않게(10-30 실물 init.memory_paths)
    work = Path(tempfile.mkdtemp(prefix="clinic-chat-cli-"))
    boot = timer.open_stage("cli_boot")
    t_spawn = time.perf_counter()
    try:
        env = child_env(mcp_output_tokens=spec.result_max if spec.provider == "claude-cli" else None)
        if spec.provider == "claude-cli":
            ing: Any = ClaudeIngest(res, timer, on_text, boot, collect_node_ids, mcp)
            cmd = claude_args(spec, system, mcp, schema)
            stdin_text = prompt
        else:
            home = prepare_codex_home(CACHE_DIR / "codex-home", CODEX_AUTH)
            env["CODEX_HOME"] = str(home)
            instr = work / "instructions.md"
            instr.write_text(system, encoding="utf-8")
            sf = of = None
            if schema:
                sf, of = work / "schema.json", work / "out.json"
                sf.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            ing = CodexIngest(res, timer, on_text, boot, collect_node_ids, mcp)
            cmd = codex_args(spec, instr, str(work), prompt, mcp, sf, of)
            stdin_text = None
        proc = await run_cli(cmd, cwd=str(work), env=env, timeout_s=spec.timeout_s, on_line=ing.line,
                             stdin_text=stdin_text)
        if hasattr(ing, "finish"):
            ing.finish()
        cli.update({"returncode": proc.returncode, "spawn_to_exit_ms": round((time.perf_counter() - t_spawn) * 1000, 1)})
        if proc.timed_out:
            res["error"] = res["error"] or f"시간 초과({spec.timeout_s:g}초)"
        elif proc.returncode not in (0, None) and not res["error"]:
            res["error"] = f"{spec.provider} 종료 코드 {proc.returncode}: {' | '.join(proc.stderr_tail)[:300]}"
        if spec.provider == "codex-cli" and schema and of is not None and of.is_file():
            try:
                res["structured"] = json.loads(of.read_text(encoding="utf-8"))
            except ValueError:
                pass
    except Exception as e:  # noqa: BLE001 — 그 열만 오류
        res["error"] = res["error"] or f"{type(e).__name__}: {e}"[:300]
    finally:
        timer.close_open()
        timer.finish()
        res["timing"] = _timing(timer, spec)
        summarize_calls(res["trace"])
        shutil.rmtree(work, ignore_errors=True)
    return res


GAP = "(측정 밖)"


def fill_gaps(stages: list[dict], total_ms: float | None) -> list[dict]:
    """0 ~ total 중 어느 단계도 덮지 않은 구간을 「(측정 밖)#k」 단계로 — 덮인 합 + 빈틈 = total(10-30 피드백)."""
    if total_ms is None:
        return stages
    spans = sorted((s["start_ms"], s["end_ms"]) for s in stages if s.get("end_ms") is not None)
    gaps, cur = [], 0.0
    for a, b in spans:
        if a > cur + 1:
            gaps.append((cur, a))
        cur = max(cur, b)
    if total_ms > cur + 1:
        gaps.append((cur, total_ms))
    out = list(stages)
    for k, (a, b) in enumerate(gaps, 1):
        out.append({"name": f"{GAP}#{k}", "start_ms": round(a, 1), "end_ms": round(b, 1), "gap": True})
    return sorted(out, key=lambda s: (s["start_ms"], s.get("end_ms") or 0))


def coverage(stages: list[dict]) -> float:
    """단계들이 덮는 시간(겹침은 한 번만)."""
    spans = sorted((s["start_ms"], s["end_ms"]) for s in stages if s.get("end_ms") is not None)
    tot, cur_a, cur_b = 0.0, None, None
    for a, b in spans:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                tot += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        tot += cur_b - cur_a
    return round(tot, 1)


def _timing(timer: CaseTimer, spec: CliSpec) -> dict:
    t = timer.as_dict()
    t["stages"] = fill_gaps([s for s in t["stages"] if not s.get("gap")], t.get("total_ms"))
    t["thinking_ms"] = round(sum(s.get("thinking_ms") or 0 for s in t["stages"]), 1) or None
    boot = next((s for s in t["stages"] if s["name"] == "cli_boot" and s.get("end_ms") is not None), None)
    b = round(boot["end_ms"] - boot["start_ms"], 1) if boot else None
    t.update({"cli_boot_ms": b, "ttft_kind": "token" if spec.provider == "claude-cli" else "message",
              "ttft_ms_ex_boot": round(t["ttft_ms"] - b, 1) if t.get("ttft_ms") is not None and b is not None else None,
              "total_ms_ex_boot": round(t["total_ms"] - b, 1) if t.get("total_ms") is not None and b is not None else None})
    return t
