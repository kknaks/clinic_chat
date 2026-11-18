"""② 이해 호출(SPEC-008 §2-2 UC1 ~ UC10 · §4-1 FL1 · FL3 · FL4 · WORK-017 WP-2).

- `claude -p` 한 번 · 세션을 남기지 않는다(`--no-session-persistence` — `--resume` · `--session-id` · `--fallback-model` 없음) ·
  격리 인자는 답 실행기와 같은 값(`llm/cli.py` 의 `DISALLOWED_TOOLS` 를 그대로 import — 두 벌 금지) · 턴 메시지는 stdin ·
  cwd = 답 실행기의 고정 cwd(UC2). thinking 끔(`CHAT_PICK_THINKING=off`) = `--settings {"alwaysThinkingEnabled": false}` +
  env `MAX_THINKING_TOKENS=0`(`llm/cli.py` 의 추론 끔 꼴 — E8-0 실측으로 정함).
- 시스템 글 = `pick.rules[]` 를 줄바꿈으로 이은 글 + 빈 줄 + 목록 글(UC5 — 턴 · 대화와 무관).
- 턴 메시지 = 세 블록(UC6 · UC7 · FL4): 대화(성공 턴 전부 · 가까운 n 턴에만 앞 답 요약 · 보여 준 리스트) · 객체 · 이번 말.
- 실패(FL1): 시간 초과 → `timeout` · 종료 코드 · 인증 · 프로세스 → `cli` · 재시도 0(FL3). JSON 깨짐은 `verify.parse` 가 가른다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from clinic_chat.consult.entry_list import EntryList
from clinic_chat.llm.cli import DISALLOWED_TOOLS, OFF_SETTINGS
from clinic_chat.llm.cli_process import child_env, run_cli

THINKING_OFF_ENV = {"MAX_THINKING_TOKENS": "0"}


@dataclass
class Raw:
    output_raw: str = ""
    ms: float | None = None
    api_ms: float | None = None
    usage: dict = field(default_factory=dict)
    models: list[str] = field(default_factory=list)
    error: dict | None = None


def system_text(pick: dict, el: EntryList) -> str:
    return "\n".join(pick["rules"]) + "\n\n" + el.text


def args(s, system: str) -> list[str]:
    a = [s.claude_cli_bin, "-p", "--model", s.chat_pick_model, "--effort", s.chat_pick_effort, "--max-turns", "1",
         "--output-format", "json", "--no-session-persistence", "--system-prompt", system,
         "--setting-sources", "", "--tools", "", "--disallowedTools", DISALLOWED_TOOLS,
         "--disable-slash-commands", "--strict-mcp-config", "--permission-mode", "bypassPermissions"]
    if s.chat_pick_thinking == "off":
        a += ["--settings", json.dumps(OFF_SETTINGS)]
    return a


def env(s) -> dict[str, str]:
    return child_env(dict(THINKING_OFF_ENV) if s.chat_pick_thinking == "off" else {})


def _nm(el: EntryList | None, table: str, code: str, name: str = "") -> str:
    n = name or (el.name(table, code) if el else "")
    return f"{n}({table}:{code})"


def _list_text(lst: dict, max_n: int, el) -> str:
    """장부 `list` 를 화면에 그린 차례 — ① 관심 대상 · ② 계열 → 시술(내려온 시술 포함) · ③ 시너지. 번호는 손님이 세는
    항목 줄(관심 대상 · 시술 · 시너지 상대)에만 단다 — 계열은 번호 없는 머리 `[이름(table:code)]`(E8 1회 고침: 계열 머리에도
    번호를 달면 「세 번째」 가 화면의 셋째 시술이 아니라 계열을 가리켰다). 앞에서 `max_n` 개."""
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    k = 0

    def item(t: str, c: str, n: str) -> None:
        nonlocal k
        if t and c and (t, c) not in seen and k < max_n:
            seen.add((t, c))
            k += 1
            parts.append(f"{k}) {_nm(el, t, c, n)}")
    for x in lst.get("interest") or []:
        item(x.get("table") or "", x.get("code") or "", x.get("name") or "")
    for c in lst.get("concerns") or []:
        for m in [*(c.get("modalities") or []), *(c.get("descended") or [])]:
            if k >= max_n:
                break
            if m.get("modality_code"):
                parts.append(f"[{_nm(el, 'modality', m['modality_code'], m.get('modality_name') or '')}]")
            for p in m.get("procedures") or []:
                item("procedure", p.get("procedure_code") or "", p.get("procedure_name") or "")
    for b in lst.get("synergy") or []:
        for p in [*(b.get("inside") or []), *(b.get("outside") or [])]:
            item(p.get("table") or "", p.get("code") or "", p.get("name") or "")
    return " ".join(parts) if k else ""


def _ok(row: dict) -> bool:
    u = (row.get("consult") or {}).get("understand")
    return row.get("error") is None and u is not None and u.get("error") is None


def turn_message(turns: list[dict], obj: dict, question: str, pick: dict, s, el: EntryList | None = None) -> str:
    """UC6 — `turns` = 이 대화의 맺는 줄(차례대로 · 켠 줄만 · 끝난 턴만)."""
    h, mk = pick["headers"], pick["marks"]
    rows = [t for t in turns if (t.get("consult") or {}).get("understand") is not None or t.get("error") is not None]
    ok_idx = [i for i, t in enumerate(rows) if _ok(t)]
    near = set(ok_idx[-s.chat_pick_context_turns:]) if s.chat_pick_context_turns > 0 else set()
    lines: list[str] = []
    if rows:
        lines.append(h["history"])
        had_list = False
        for i, t in enumerate(rows):
            q = t.get("question") or ""
            if not _ok(t):  # FL4 — 실패 턴은 손님 말 + 표지
                lines.append(f"{t['turn']}. {h['user']} {q} {mk['failed']}")
                continue
            c = t.get("consult") or {}
            u = c.get("understand") or {}
            acc = u.get("accepted") or {}
            picked = ", ".join(_nm(el, x["table"], x["code"], x.get("name") or "") for x in acc.get("targets") or [])
            kind = (c.get("turn_kind") or {}).get("kind") or acc.get("kind") or ""
            lines.append(f"{t['turn']}. {h['user']} {q}")
            lines.append(f"   {h['picked']} {picked or mk['none']} · {kind}")
            ask = c.get("ask")
            if ask and ask.get("options"):
                lines.append(f"   {h['options']} " + " / ".join(
                    f"{k}) {_nm(el, o.get('table') or '', o.get('code') or '', o.get('name') or '')}"
                    for k, o in enumerate(ask["options"], 1)))
            if i in near:
                ans = (t.get("answer") or "")[:s.chat_pick_answer_chars]
                if ans:
                    lines.append(f"   {h['answer']} {ans}")
                lst = t.get("list")
                if lst is not None:
                    txt = _list_text(lst, s.chat_pick_list_max, el)
                    if txt:
                        lines.append(f"   {h['list']} {txt}")
                elif had_list:
                    lines.append(f"   {h['list']} {mk['same_list']}")
            if t.get("list") is not None:
                had_list = True
    lines.append(h["object"])
    axis = [x for sl in ("concerns", "sites", "subjects") for x in obj.get(sl) or []
            if x.get("table") and x.get("state") in ("said", "confirmed")]
    guess = [x for sl in ("concerns", "sites", "subjects") for x in obj.get(sl) or []
             if x.get("table") and x.get("state") == "guess"]
    gone = [x for sl in ("concerns", "sites", "subjects") for x in obj.get(sl) or []
            if x.get("table") and x.get("state") == "withdrawn"]
    for head, xs in ((h["obj_targets"], axis), (h["obj_guess"], guess), (h["obj_withdrawn"], gone)):
        lines.append(f"{head} " + (", ".join(_nm(el, x["table"], x["code"], x.get("name") or "") for x in xs)
                                   or mk["none"]))
    lines.append(h["utterance"])
    lines.append(question)
    return "\n".join(lines)


def _cli_out(text: str) -> dict | None:
    try:
        d = json.loads(text)
    except ValueError:
        return None
    if isinstance(d, list):
        d = next((x for x in reversed(d) if isinstance(x, dict) and x.get("type") == "result"), None)
    return d if isinstance(d, dict) else None


async def call(s, system: str, message: str, *, argv: list[str] | None = None,
               child: dict[str, str] | None = None) -> Raw:
    """UC2 · UC9 · FL1 · FL3 — 한 번만 부른다(재시도 0). `argv` · `child` = 실측(E8-0)에서 인자 · env 를 바꿀 때만."""
    t0 = time.perf_counter()
    buf: list[str] = []
    out = Raw()
    try:
        res = await run_cli(argv or args(s, system), cwd=str(s.chat_cli_cwd_path()), env=child if child is not None else env(s),
                            timeout_s=s.chat_pick_timeout_sec, on_line=buf.append, stdin_text=message)
    except Exception as e:  # noqa: BLE001 — 프로세스를 못 띄움 = cli
        out.ms = round((time.perf_counter() - t0) * 1000, 1)
        out.error = {"kind": "cli", "detail": type(e).__name__}
        return out
    out.ms = round((time.perf_counter() - t0) * 1000, 1)
    if res.timed_out:
        out.error = {"kind": "timeout", "detail": f"{s.chat_pick_timeout_sec}s"}
        return out
    d = _cli_out("".join(buf))
    if res.returncode != 0 or d is None or d.get("is_error"):
        tail = (res.stderr_tail or [""])[-1][:200] if res.stderr_tail else ""
        out.error = {"kind": "cli", "detail": f"exit={res.returncode}" + (f" {tail}" if tail else "")}
        if d is not None:
            out.output_raw = str(d.get("result") or "")[:2000]
        return out
    out.output_raw = str(d.get("result") or "")
    out.api_ms = d.get("duration_api_ms")
    u = d.get("usage") or {}
    th = (u.get("output_tokens_details") or {}).get("thinking_tokens")
    out.usage = {"input_tokens": u.get("input_tokens"), "cache_read_input_tokens": u.get("cache_read_input_tokens"),
                 "output_tokens": u.get("output_tokens"), "thinking_tokens": th}
    out.models = list((d.get("modelUsage") or {}).keys())
    return out
