"""대화 장부(SPEC-004 §5-1 LG1 ~ LG5 · §3-4 CL13 · CL14 · WORK-011 §9 · 검수 W-3 · W-4).

- `CHAT_DIR/turns.jsonl` 한 파일 · 덧붙이기만(`runs.RunStore._append` 꼴) · `runs.jsonl` 과 섞지 않는다.
- 턴마다 줄 **둘**: 띄우기 전에 시작 줄(`phase: "start"`) · 끝나면(성공 · 오류 모두) 맺는 줄(`phase: "end"`).
  LG1 의 「턴 하나에 한 줄」 은 맺는 줄이다.
- 되읽기(LG4): 같은 `conv_id` 로 오면 파일에서 메모리를 세운다. 시작 줄만 있고 맺는 줄이 없는 턴이 있으면 `broken`.
- 「실은 gid → 줄 번호」 · `next_n` · 요약 줄 장부는 `error` 가 `null` 인 맺는 줄만 센다(W-3 ②).
- 턴 한도(CL13)는 CLI 가 답한 턴(`llm.error` 가 `null` · 띄운 턴)으로 센다(검수 N-5).
- 키 · 홈 경로 · 세션 파일 경로 · cwd 경로를 적지 않는다(LG5) — 줄을 쓰기 전에 홈 경로를 `~` 로 바꾼다.
- 0.2(SPEC-005 §10-1 · WORK-012 §6 · B-2): 시작 줄에 `consult_state`(대화 첫 턴의 값 — 그 대화는 끝까지 그 값) · 그 칸이
  없는 대화(0.1 로 쓴 줄)는 끈 상태로 끝까지. 객체(`Conv.consult`)는 `error` 가 없는 맺는 줄의 `consult.after` 로만 선다(RV9).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from clinic_chat.consult.prompt import ConsultPrompt
from clinic_chat.llm.chat import ChatConv
from clinic_chat.runs import now_iso

TURNS_FILE = "turns.jsonl"
# LG3 의 굵은 칸 여섯(턴 · session_id · 고른 대상 · 읽은 재료 id · 답 · 시간)
BOLD = ("turn", "llm.session_id", "targets", "material.new_lines", "answer", "timing")
END_KINDS = ("broken", "build_changed")  # 맺는 줄의 `conv_end` — 그 대화는 끝


def _scrub_home(text: str) -> str:
    return text.replace(str(Path.home()), "~")


@dataclass
class Conv:
    conv_id: str
    chat: ChatConv
    prompt: ConsultPrompt | None = None
    prompt_sha: str | None = None
    turns: list[dict] = field(default_factory=list)  # 맺는 줄(차례대로)
    starts: dict[int, dict] = field(default_factory=dict)  # 시작 줄
    gold_hash: str | None = None
    sent: dict[str, int] = field(default_factory=dict)  # 실은 gid → 대화 줄 번호
    summaries: dict[str, int] = field(default_factory=dict)  # 요약 줄 키 → 줄 번호(B-6)
    next_n: int = 1
    ended: str | None = None  # broken | build_changed
    running: int | None = None  # 지금 도는 턴 번호
    consult_state: str | None = None  # 0.2 — "on" | "off"(첫 턴 시작 줄의 값 · 없으면 "off")
    consult: dict | None = None  # 0.2 — 상담 객체(마지막 성공 줄의 `consult.after`)
    memo: dict = field(default_factory=dict)  # 0.2 — 객체 조회 응답(Call.key → Read 목록 · 메모리만 · B-10)

    @property
    def n_started(self) -> int:
        return max([0, *self.starts])

    def open_turns(self) -> list[int]:
        done = {t["turn"] for t in self.turns}
        return sorted(n for n in self.starts if n not in done)

    def answered(self) -> int:
        """CL13 — CLI 가 답한 턴 수."""
        return sum(1 for t in self.turns if (t.get("llm") or {}).get("answered"))

    def last_list(self) -> dict | None:
        return next((t["list"] for t in reversed(self.turns) if t.get("list") is not None), None)

    def last_sites(self) -> dict | None:
        return next((t["sites"] for t in reversed(self.turns) if t.get("sites") is not None), None)


class Ledger:
    def __init__(self, chat_dir: Path) -> None:
        self.dir = Path(chat_dir)
        self.convs: dict[str, Conv] = {}

    @property
    def file(self) -> Path:
        return self.dir / TURNS_FILE

    def _append(self, row: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.file.open("a", encoding="utf-8") as f:
            f.write(_scrub_home(json.dumps(row, ensure_ascii=False)) + "\n")

    def rows(self, conv_id: str | None = None) -> list[dict]:
        if not self.file.is_file():
            return []
        out = []
        for ln in self.file.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if conv_id is None or r.get("conv_id") == conv_id:
                out.append(r)
        return out

    def new_conv(self, prompt: ConsultPrompt | None = None, conv_id: str | None = None) -> Conv:
        c = Conv(conv_id or uuid.uuid4().hex[:16], ChatConv(conv_id or ""), prompt, prompt.sha256 if prompt else None)
        c.chat.conv_id = c.conv_id
        self.convs[c.conv_id] = c
        return c

    def get(self, conv_id: str) -> Conv | None:
        """메모리 → 없으면 파일에서 되읽기(LG4) · 줄이 없으면 None."""
        if conv_id in self.convs:
            return self.convs[conv_id]
        rows = self.rows(conv_id)
        if not rows:
            return None
        c = Conv(conv_id, ChatConv(conv_id))
        for r in rows:
            if r.get("phase") == "start":
                c.starts[int(r["turn"])] = r
                if c.consult_state is None:
                    c.consult_state = r.get("consult_state") or "off"  # B-2 — 칸 없는 대화는 끈 상태로 끝까지
                c.prompt_sha = c.prompt_sha or (r.get("prompts") or {}).get("consult_at_start")
                sid = (r.get("llm") or {}).get("session_id")
                if sid:
                    c.chat.session_id = sid
            elif r.get("phase") == "end":
                self._apply_end(c, r)
        if c.open_turns():  # W-3 ① — 서버가 턴 도중 죽었다(CLI 자식은 세션에 턴을 남겼을 수 있다)
            c.chat.state = "broken"
            c.ended = "broken"
        self.convs[conv_id] = c
        return c

    def start(self, conv: Conv, question: str, consult_state: str | None = None) -> dict:
        n = conv.n_started + 1
        if conv.consult_state is None:
            conv.consult_state = state_at_start(conv, consult_state)
        row = {"conv_id": conv.conv_id, "turn": n, "phase": "start", "asked_at": now_iso(), "question": question,
               "llm": {"session_id": conv.chat.session_id, "mode": "new" if conv.chat.state == "new" else "resume"},
               "prompts": {"consult_at_start": conv.prompt_sha}, "consult_state": conv.consult_state}
        conv.starts[n] = row
        conv.running = n
        self._append(row)
        return row

    def end(self, conv: Conv, row: dict) -> dict:
        row = {"conv_id": conv.conv_id, "phase": "end", **row}
        self._append(row)
        self._apply_end(conv, row)
        conv.running = None
        return row

    @staticmethod
    def _apply_end(c: Conv, r: dict) -> None:
        c.turns.append(r)
        llm = r.get("llm") or {}
        nxt = r.get("session_next") or {}
        if nxt.get("session_id"):
            c.chat.session_id = nxt["session_id"]
        if r.get("session_state"):
            c.chat.state = r["session_state"]
        if c.gold_hash is None and (r.get("build") or {}).get("gold_hash") and llm.get("answered"):
            c.gold_hash = r["build"]["gold_hash"]
        c.prompt_sha = c.prompt_sha or (r.get("prompts") or {}).get("consult_at_start")
        if r.get("error") is None:
            mat = r.get("material") or {}
            for ln in mat.get("new_lines") or []:
                if ln.get("summary"):
                    if ln.get("key"):
                        c.summaries[ln["key"]] = ln["n"]
                elif ln.get("gid"):
                    c.sent[str(ln["gid"])] = ln["n"]
            if mat.get("next_n"):
                c.next_n = mat["next_n"]
            after = (r.get("consult") or {}).get("after")
            if after is not None:  # RV9 — 성공 줄만
                c.consult = after
        if r.get("conv_end") in END_KINDS:
            c.ended = r["conv_end"]
        if c.chat.state == "broken":
            c.ended = "broken"


def state_at_start(conv: Conv, setting: str | None) -> str:
    """대화의 켠 / 끈 상태 — 이미 정해졌으면 그 값 · 첫 턴이면 설정값 · 시작 줄이 있는데 값이 없으면(0.1 대화) `off`."""
    if conv.consult_state is not None:
        return conv.consult_state
    if conv.starts:
        return "off"
    return setting if setting in ("on", "off") else "off"


def blocked(conv: Conv, max_turns: int) -> str | None:
    """TF1 — 받지 않는 까닭(오류 종류 코드) · 받으면 None."""
    if conv.ended:
        return conv.ended
    if conv.running is not None or conv.chat.running:
        return "busy"
    if conv.answered() >= max_turns:
        return "turn_limit"
    return None
