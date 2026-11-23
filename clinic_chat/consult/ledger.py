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
- 0.3(SPEC-006 FX3 · WORK-014 §6): 켠 대화의 시작 줄에 `consult_rev`(이 판 = `CONSULT_REV`) — 첫 턴에 정하고 끝까지.
  끈 대화의 시작 줄은 0.2 그대로(칸 없음). 되읽기는 첫 시작 줄에서 세운다(없으면 `None`).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from clinic_chat.consult.drawer import DrawerIndex
from clinic_chat.consult.prompt import ConsultPrompt
from clinic_chat.llm.chat import ChatConv
from clinic_chat.runs import now_iso

TURNS_FILE = "turns.jsonl"
# LG3 의 굵은 칸 여섯(턴 · session_id · 고른 대상 · 읽은 재료 id · 답 · 시간)
BOLD = ("turn", "llm.session_id", "targets", "material.new_lines", "answer", "timing")
END_KINDS = ("broken", "build_changed")  # 맺는 줄의 `conv_end` — 그 대화는 끝
CONSULT_REV = 4  # 0.4 — 켠 대화의 판(SPEC-008 §6-1 · rev 3 켠 대화는 0.4 서버에서 끝난 대화)


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
    sent_names: dict[str, int] = field(default_factory=dict)  # SPEC-011 G7 · LG6 — 이름만 실은 gid → 줄 번호(뒤 턴 상세는 같은 번호로)
    summaries: dict[str, int] = field(default_factory=dict)  # 요약 줄 키 → 줄 번호(B-6)
    next_n: int = 1
    ended: str | None = None  # broken | build_changed
    running: int | None = None  # 지금 도는 턴 번호
    consult_state: str | None = None  # 0.2 — "on" | "off"(첫 턴 시작 줄의 값 · 없으면 "off")
    consult: dict | None = None  # 0.2 — 상담 객체(마지막 성공 줄의 `consult.after`)
    memo: dict = field(default_factory=dict)  # 0.2 — 객체 조회 응답(Call.key → Read 목록 · 메모리만 · B-10)
    consult_rev: int | None = None  # 0.3 — 켠 대화의 판(첫 시작 줄의 값 · 없으면 None)
    list_calls: list = field(default_factory=list)  # 0.4 QS4 — 마지막 리스트 ② 의 조회(메모리만 · 장부에 안 적음)
    viewed: list = field(default_factory=list)  # 0.5 MD4 — 마지막 맺는 줄 뒤에 모달로 본 진료항목(다음 턴 블록)
    cart: list = field(default_factory=list)  # 0.5 D-15 — 장바구니(마지막 `cart` 줄로 되읽음)
    topic_override: str | None = None  # 0.5 TP10 — 마지막 맺는 줄 뒤의 `topic_switch`(머리 누름)가 덮은 지금 주제

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

    def last_topics(self, view: bool = True) -> tuple[list[dict], str | None]:
        """0.5 TP10 — 마지막 맺는 줄의 `consult.topics` · `active_topic`. `view` 면 그 뒤 `topic_switch`(머리 누름)가 덮는다(화면).
        11-23 — 턴 판단(`view=False`)은 머리 누름을 쓰지 않는다: 누름은 보기만이고, 대화의 지금 주제는 마지막 턴의 주제다
        (conv 67bb9faf 3 턴 — 머리를 눌러 본 뒤 말한 「써마지」 가 대화 주제 ② 가 아니라 누른 ① 을 깊게 했다)."""
        for t in reversed(self.turns):
            c = t.get("consult") or {}
            if c.get("topics") is not None:
                return list(c["topics"]), (self.topic_override if view else None) or c.get("active_topic")
        return [], None

    def last_items(self) -> dict | None:
        """0.5 — 마지막 `items` 칸(목록 진료항목 코드 — SPEC-010 §5-1 ⑦)."""
        return next((t["items"] for t in reversed(self.turns) if t.get("items") is not None), None)

    def last_sites(self) -> dict | None:
        return next((t["sites"] for t in reversed(self.turns) if t.get("sites") is not None), None)


class Ledger:
    def __init__(self, chat_dir: Path) -> None:
        self.dir = Path(chat_dir)
        self.convs: dict[str, Conv] = {}
        self.kos_map: dict | None = None  # 0.5 JS3 · JS4 — 시작 줄 `kos_map{sha, made_at, hub_gold_hash, hash_mismatch}`
        self.index = DrawerIndex.scan(self.file)  # 0.3 W-11 — 뜰 때 한 번 훑고 줄을 쓸 때 함께 고친다

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
                    c.consult_rev = r.get("consult_rev")  # 0.3 FX3 — 첫 시작 줄의 값
                c.prompt_sha = c.prompt_sha or (r.get("prompts") or {}).get("consult_at_start")
                sid = (r.get("llm") or {}).get("session_id")
                if sid:
                    c.chat.session_id = sid
            elif r.get("phase") == "end":
                self._apply_end(c, r)
            elif r.get("phase") == "topic_switch":  # 0.5 TP10 · r6 P2-3
                c.topic_override = r.get("topic_id")
            elif r.get("phase") == "cart":  # 0.5 CT6 — 새로 고침 · 재시작 뒤에도 같은 장바구니
                c.cart = list(r.get("cart") or [])
            elif r.get("phase") == "viewed" and r.get("code") and r["code"] not in c.viewed:  # 0.5 MD4
                c.viewed.append(r["code"])
        if c.open_turns():  # W-3 ① — 서버가 턴 도중 죽었다(CLI 자식은 세션에 턴을 남겼을 수 있다)
            c.chat.state = "broken"
            c.ended = "broken"
        self.convs[conv_id] = c
        return c

    def start(self, conv: Conv, question: str, consult_state: str | None = None) -> dict:
        n = conv.n_started + 1
        if conv.consult_state is None:
            conv.consult_state = state_at_start(conv, consult_state)
            if conv.consult_state == "on" and not conv.starts:  # 0.3 FX3 — 켠 새 대화만 · 첫 턴에 정함
                conv.consult_rev = CONSULT_REV
        row = {"conv_id": conv.conv_id, "turn": n, "phase": "start", "asked_at": now_iso(), "question": question,
               "llm": {"session_id": conv.chat.session_id, "mode": "new" if conv.chat.state == "new" else "resume"},
               "prompts": {"consult_at_start": conv.prompt_sha}, "consult_state": conv.consult_state}
        if conv.consult_rev is not None:
            row["consult_rev"] = conv.consult_rev
        if self.kos_map is not None:
            row["kos_map"] = dict(self.kos_map)
        conv.starts[n] = row
        conv.running = n
        self._append(row)
        self.index.on_start(row)
        return row

    def viewed(self, conv: Conv, code: str, figs: list[dict], error: dict | None) -> dict:
        """0.5 MD4 · IM12 — 모달을 연 진료항목(장부 줄 `viewed` · 턴 아님)."""
        row = {"conv_id": conv.conv_id, "phase": "viewed", "at": now_iso(), "code": code,
               "modal": {"figs": figs, "error": error}}
        self._append(row)
        if code not in conv.viewed:
            conv.viewed.append(code)
        return row

    def cart(self, conv: Conv, cart: list[dict], op: str, rec: dict | None = None) -> dict:
        """0.5 CT6 — 장바구니가 바뀔 때마다(진료항목 코드 · 옵션 키만 · 추천 옵션 키 · 표지 — 가격 0)."""
        row = {"conv_id": conv.conv_id, "phase": "cart", "at": now_iso(), "op": op, "cart": cart, "rec": rec}
        self._append(row)
        conv.cart = cart
        return row

    def cart_saved(self, conv: Conv, codes: list[str], best: list[list[str]] | None) -> dict:
        """0.5 CT7 — 「저장」(코드 목록 · 추천 최저 조합의 옵션 키 · 시각 · 가격 0)."""
        row = {"conv_id": conv.conv_id, "phase": "cart_saved", "at": now_iso(), "codes": codes, "best_keys": best}
        self._append(row)
        return row

    def topic_switch(self, conv: Conv, topic_id: str) -> dict:
        """0.5 TP3 1 · TP10 — 앞 주제 머리 누름(턴 아님) · 장부 줄 `topic_switch`."""
        row = {"conv_id": conv.conv_id, "phase": "topic_switch", "at": now_iso(), "topic_id": topic_id}
        self._append(row)
        conv.topic_override = topic_id
        return row

    def end(self, conv: Conv, row: dict) -> dict:
        row = {"conv_id": conv.conv_id, "phase": "end", **row}
        self._append(row)
        self.index.on_end(row)
        self._apply_end(conv, row)
        conv.running = None
        return row

    @staticmethod
    def _apply_end(c: Conv, r: dict) -> None:
        c.turns.append(r)
        c.viewed = []  # 0.5 MD4 — 본 진료항목은 그 다음 한 턴의 블록에만
        if (r.get("consult") or {}).get("topics") is not None:
            c.topic_override = None  # 새 맺는 줄의 `active_topic` 이 앞 머리 누름을 덮는다
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
                elif ln.get("gid") and ln.get("names_only"):  # G7 — 이름만은 `sent` 에 넣지 않는다
                    c.sent_names[str(ln["gid"])] = ln["n"]
                elif ln.get("gid"):
                    c.sent[str(ln["gid"])] = ln["n"]
                    c.sent_names.pop(str(ln["gid"]), None)
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
    """대화의 켠 / 끈 상태 — 이미 정해졌으면 그 값 · 시작 줄이 있는데 값이 없으면(0.1 대화) `off` · **새 대화(시작 줄 0)는 늘
    `on`**(SPEC-008 CP1 · S8-T19 — 설정값이 `off` 여도 · 끈 상태로 시작하는 새 대화는 없다). `setting` 은 옛 부르는 꼴 그대로 받는다."""
    if conv.consult_state is not None:
        return conv.consult_state
    if conv.starts:
        return "off"
    return "on"


def blocked(conv: Conv, max_turns: int) -> str | None:
    """TF1 — 받지 않는 까닭(오류 종류 코드) · 받으면 None."""
    if conv.ended:
        return conv.ended
    if conv.starts and conv.consult_state == "on" and (conv.consult_rev or 0) < CONSULT_REV:
        return "rev_changed"  # 0.3 FX4 · 0.4 C8-4 — 앞 판으로 시작한 켠 대화는 끝난 대화(줄을 쓰지 않는다 — B-5)
    if conv.starts and conv.consult_state == "off":
        return "rev_changed"  # 0.4 CP7 · C8-8 — 옛 끈 대화는 읽기만(같은 자리 · 같은 꼴 · 줄을 쓰지 않는다)
    if conv.running is not None or conv.chat.running:
        return "busy"
    if conv.answered() >= max_turns:
        return "turn_limit"
    return None
