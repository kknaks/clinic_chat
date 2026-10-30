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

from clinic_chat.cases.loop import node_ids
from clinic_chat.config import ROOT
from clinic_chat.llm import auth
from clinic_chat.llm.cli_process import StopRun, child_env, run_cli
from clinic_chat.timing import CaseTimer

MCP_PREFIX = "mcp__"
STRUCTURED_TOOL = "StructuredOutput"
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


@dataclass
class McpAttach:
    name: str  # 서버 id(도구 이름 접두 mcp__<name>__)
    url: str | None = None  # Streamable HTTP(허브)
    command: str | None = None  # stdio(채점용 로컬 원장 MCP)
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


def new_result(spec: CliSpec) -> dict:
    return {"answer": "", "stop_reason": None, "loop_capped": False, "error": None, "refusal": False,
            "timing": {}, "tokens": {}, "trace": {"tool_calls": [], "cli": {"provider": spec.provider}},
            "cost": {"usd": 0.0, "by_stage": {}, "billed": "subscription"}, "structured": None}


# ───────────── Claude ─────────────

def claude_args(spec: CliSpec, system: str, prompt: str, mcp: McpAttach | None, schema: dict | None) -> list[str]:
    a = [spec.bin, "-p", "--model", spec.model, "--effort", spec.effort, "--system-prompt", system,
         "--setting-sources", "", "--tools", "", "--disable-slash-commands", "--strict-mcp-config",
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
    return [*a, prompt]


class ClaudeIngest:
    """`claude -p --output-format stream-json --verbose --include-partial-messages` 줄 → timer · 결과."""

    def __init__(self, res: dict, timer: CaseTimer, on_text: Callable[[str], None], boot: dict,
                 collect_node_ids: bool, mcp: McpAttach | None) -> None:
        self.res, self.timer, self.on_text, self.boot = res, timer, on_text, boot
        self.collect, self.mcp = collect_node_ids, mcp
        self.cli = res["trace"]["cli"]
        self.round: dict | None = None
        self.n_round = 0
        self.n_tool = 0
        self.round_first: float | None = None
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
            self.cli["rate_limits"].append({k: info.get(k) for k in
                                            ("status", "rateLimitType", "utilization", "resetsAt") if k in info})
            if str(info.get("status", "")).lower() in ("rejected", "blocked"):
                self.res["error"] = f"한도: {info.get('rateLimitType')} · 다시 열림 {info.get('resetsAt')}"
                self.cli["rate_limited"] = True
        elif t == "result":
            self._result(ev)

    def _init(self, ev: dict) -> None:
        self.timer.close_stage(self.boot)
        self.cli.update({"session_tools": ev.get("tools"), "mcp_servers": ev.get("mcp_servers"),
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

    def _stream(self, e: dict) -> None:
        et = e.get("type")
        if et == "message_start":
            if self.boot.get("end_ms") is None:
                self.timer.close_stage(self.boot)
            self.n_round += 1
            self.round = self.timer.open_stage(f"claude#{self.n_round}")
            self.round_first = None
            u = (e.get("message") or {}).get("usage")
            if u:
                self.round["tokens"] = _claude_usage(u)
        elif et == "content_block_delta":
            d = e.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                ms = self.timer.mark_text()
                if self.round_first is None:
                    self.round_first = ms
                    self.timer.ttft_answer_ms = ms
                self.res["answer"] += d["text"]
                self.on_text(d["text"])
            elif d.get("type") in ("thinking_delta", "signature_delta"):
                self.cli["thinking_seen"] = True
        elif et == "content_block_start" and (e.get("content_block") or {}).get("type") == "thinking":
            self.cli["thinking_seen"] = True
        elif et == "message_delta":
            if self.round is not None and e.get("usage"):
                self.round["tokens"] = {**(self.round.get("tokens") or {}), **_claude_usage(e["usage"])}
            if (e.get("delta") or {}).get("stop_reason"):
                self.res["stop_reason"] = e["delta"]["stop_reason"]
        elif et == "message_stop" and self.round is not None:
            self.timer.close_stage(self.round)
            self.timer.llm_round_trips += 1
            if self.round_first is None:
                self.timer.ttft_answer_ms = None
            self.round = None

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
            self.n_tool += 1
            st = self.timer.open_stage(f"tool:{short}#{self.n_tool}")
            call = {"tool": short, "input": b.get("input"), "output_bytes": None, "ms": None, "is_error": None}
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
            call.update({"output_bytes": len(text.encode("utf-8")) if text is not None else None,
                         "ms": round(st["end_ms"] - st["start_ms"], 1), "is_error": bool(b.get("is_error"))})
            if self.collect and text:
                try:
                    call["node_ids"] = node_ids(json.loads(text))
                except ValueError:
                    call["node_ids"] = []

    def _result(self, ev: dict) -> None:
        u = _claude_usage(ev.get("usage") or {})
        self.res["tokens"] = {"total": u, "by_model": {m: _claude_usage(v) for m, v in (ev.get("modelUsage") or {}).items()}}
        self.cli.update({"num_turns": ev.get("num_turns"), "duration_ms": ev.get("duration_ms"),
                         "duration_api_ms": ev.get("duration_api_ms"), "subtype": ev.get("subtype"),
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


def _claude_usage(u: dict) -> dict:
    return {k: u.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                       "cache_read_input_tokens") if k in u}


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
         "-c", "tools.view_image=false", "-c", "features.plugins=false", "-c", "features.skill_search=false",
         "-c", "features.skip_host_skill_discovery=true", "-c", 'shell_environment_policy.inherit="none"']
    if spec.service_tier:
        a += ["-c", f'service_tier="{spec.service_tier}"']
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
                 collect_node_ids: bool) -> None:
        self.res, self.timer, self.on_text, self.boot, self.collect = res, timer, on_text, boot, collect_node_ids
        self.cli = res["trace"]["cli"]
        self.turn: dict | None = None
        self.n_tool = 0
        self.tools: dict[str, tuple[dict, dict]] = {}
        self.cli.update({"thinking_seen": False, "item_types": [], "events": 0, "messages": 0})

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
            self.timer.close_stage(self.boot)
            self.turn = self.timer.open_stage("codex#1")
        elif t in ("turn.completed", "turn.failed"):
            if isinstance(ev.get("usage"), dict):
                u = ev["usage"]
                self.res["tokens"] = {"total": {"input_tokens": u.get("input_tokens", 0),
                                                "cache_read_input_tokens": u.get("cached_input_tokens", 0),
                                                "output_tokens": u.get("output_tokens", 0),
                                                "reasoning_output_tokens": u.get("reasoning_output_tokens", 0)}}
            if self.turn is not None:
                self.timer.close_stage(self.turn)
                self.timer.llm_round_trips += 1
            if t == "turn.failed":
                self._fail(ev.get("error") or ev)
            else:
                self.res["stop_reason"] = "end_turn"
        elif t == "error":
            self._fail(ev.get("message") or ev.get("error") or ev)
        elif t.startswith("item."):
            self._item(t.split(".", 1)[1], ev.get("item") or {})

    def _fail(self, err: Any) -> None:
        msg = err.get("message") if isinstance(err, dict) else str(err)
        msg = str(msg)[:300]
        if RATE_LIMIT_TEXT.search(msg):
            self.cli["rate_limited"] = True
        self.res["error"] = self.res["error"] or f"Codex CLI: {msg}"

    def _item(self, phase: str, item: dict) -> None:
        it = str(item.get("type", ""))
        if it and it not in self.cli["item_types"]:
            self.cli["item_types"].append(it)
        iid = str(item.get("id") or f"item-{self.n_tool}")
        if it == "reasoning":
            self.cli["thinking_seen"] = True
        elif it == "agent_message" and phase == "completed" and isinstance(item.get("text"), str):
            ms = self.timer.mark_text()  # 메시지 단위
            self.timer.ttft_answer_ms = ms
            text = item["text"] if not self.res["answer"] else "\n\n" + item["text"]
            self.res["answer"] += text
            self.cli["messages"] += 1
            self.on_text(text)
        elif it == "error":
            self._fail(item.get("message"))
        elif it == "mcp_tool_call" or "tool" in it or it in ("command_execution", "web_search", "file_change"):
            if iid not in self.tools:
                self.n_tool += 1
                name = str(item.get("tool") or item.get("name") or it)
                st = self.timer.open_stage(f"tool:{name}#{self.n_tool}")
                call = {"tool": name, "input": item.get("arguments"), "output_bytes": None, "ms": None,
                        "is_error": None, "item_type": it}
                self.tools[iid] = (st, call)
                self.res["trace"]["tool_calls"].append(call)
            st, call = self.tools[iid]
            if phase == "completed":
                self.timer.close_stage(st)
                result = item.get("result")
                text = json.dumps(result, ensure_ascii=False) if result is not None else None
                status = str(item.get("status", ""))
                call.update({"output_bytes": len(text.encode("utf-8")) if text else None,
                             "ms": round(st["end_ms"] - st["start_ms"], 1),
                             "is_error": status in ("failed", "error", "declined") or bool(item.get("error"))})
                if self.collect and result is not None:
                    call["node_ids"] = node_ids(result)


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
    work = Path(tempfile.mkdtemp(prefix="cli-", dir=CACHE_DIR))
    boot = timer.open_stage("cli_boot")
    t_spawn = time.perf_counter()
    try:
        env = child_env()
        if spec.provider == "claude-cli":
            ing: Any = ClaudeIngest(res, timer, on_text, boot, collect_node_ids, mcp)
            cmd = claude_args(spec, system, prompt, mcp, schema)
        else:
            home = prepare_codex_home(CACHE_DIR / "codex-home", CODEX_AUTH)
            env["CODEX_HOME"] = str(home)
            instr = work / "instructions.md"
            instr.write_text(system, encoding="utf-8")
            sf = of = None
            if schema:
                sf, of = work / "schema.json", work / "out.json"
                sf.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            ing = CodexIngest(res, timer, on_text, boot, collect_node_ids)
            cmd = codex_args(spec, instr, str(work), prompt, mcp, sf, of)
        proc = await run_cli(cmd, cwd=str(work), env=env, timeout_s=spec.timeout_s, on_line=ing.line)
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
        shutil.rmtree(work, ignore_errors=True)
    return res


def _timing(timer: CaseTimer, spec: CliSpec) -> dict:
    t = timer.as_dict()
    boot = next((s for s in t["stages"] if s["name"] == "cli_boot" and s.get("end_ms") is not None), None)
    b = round(boot["end_ms"] - boot["start_ms"], 1) if boot else None
    t.update({"cli_boot_ms": b, "ttft_kind": "token" if spec.provider == "claude-cli" else "message",
              "ttft_ms_ex_boot": round(t["ttft_ms"] - b, 1) if t.get("ttft_ms") is not None and b is not None else None,
              "total_ms_ex_boot": round(t["total_ms"] - b, 1) if t.get("total_ms") is not None and b is not None else None})
    return t
