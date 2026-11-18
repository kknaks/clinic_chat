"""상담 프롬프트 — `prompts/consult.json` 한 파일(WORK-011 §3 · `_RESUME` §2 11-17 「상담 프롬프트 = JSON 으로 불러옴」).

- 꼴 검사(키 · 절 `id` 순서 · 빈 값 0 · `tail_marker` 가 이어 붙인 글 안에) — 틀리면 `PromptError`(시작 실패).
- 이어 붙이기 = 절 안은 줄바꿈 하나 · 절 사이는 빈 줄 하나. sha = 파일 바이트의 sha256(JSON 전체 하나).
- 대화는 시작할 때 읽은 프롬프트로 끝까지 돈다 — 시작 때 `CHAT_DIR/prompts/<sha>.json` 으로 보관(있으면 안 쓴다).
- 0.2(WORK-012 §9a · 코디 W-7): `system` 다섯 절은 그대로 = 끈 상태의 시스템 글(0.1 과 글자 같음). 새 키는 **있으면** 꼴을
  검사한다 — `system_state`(절 `consult` · `ask` · `synergy` 이 순서) · `jev_front.target_option` · `jev_state.retract` ·
  `turn_headers.consult`. 켠 상태의 시스템 글 = role · rules · screen · consult · ask · synergy · material · tail.
  새 키가 다 있어야 `state_ready`(켠 대화의 시작 조건 — 아니면 서버가 오류 종류 `prompt` 로 받지 않는다).
- 0.3(SPEC-006 VO1 · §3-3 · WORK-014 §9): 켠 상태(rev 3)의 시스템 글 = `system_v3[]` 열세 절을 이어 붙인 글 · 꼬리 표식이 그
  글에 있어야 한다. `system[]` 은 그대로(끈 상태 글 = 0.1 글자) · `system_state[]` 는 있으면 꼴 검사만(안 쓴다).
  `state_ready` = 0.2 의 Jev · 머리 키 + `system_v3` · `jev_state.turn_kind{new, talk, follow}` · `kind_labels`(14) ·
  `voice_check[]`(정규식) · `turn_headers.close` · `turn_headers.prev` 가 전부 있다. 선택 키 `slots{}` = 빈 자리의 정해진 문구 값.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SECTION_IDS = ("role", "rules", "screen", "material", "tail")
JEV_KEYS = ("target", "target_carried", "kind", "gap")
HEADER_KEYS = ("judgement", "material", "question", "empty")
KEYS = ("version", "system", "jev_front", "turn_headers", "tail_marker")
STEP_KEYS = ("understand", "search", "list", "answer")  # 화면 단계 문구(선택 키 `steps` — LLM 에 안 감)
STATE_SECTION_IDS = ("consult", "ask", "synergy")  # 0.2 `system_state` — `screen` 과 `material` 사이에 든다
STATE_AFTER = "screen"
JEV_STATE_KEYS = ("retract",)
V3_SECTION_IDS = ("role", "voice", "facts", "owner", "hidden", "turn", "state", "empty", "close", "synergy", "safety",
                  "material", "tail")  # 0.3 `system_v3` 절 순서(SPEC-006 §3-2)
TURN_KIND_KEYS = ("new", "talk", "follow")  # 0.3 `jev_state.turn_kind`(TK7)
KIND_LABEL_KEYS = ("overview", "mechanism", "indication", "outcome", "lineup", "adverse", "recovery", "contraindication",
                   "avoid", "combo", "same_day", "order_interval", "package", "offer")  # SPEC-001 §5-8 의 14
V3_HEADER_KEYS = ("close", "prev")
PICK_HEADER_KEYS = ("history", "object", "utterance")  # 0.4 `pick.headers`(SPEC-008 §7 — 시안 글자)
PICK_HEADER_EXTRA = ("answer", "list", "picked", "options", "obj_targets", "obj_guess", "obj_withdrawn", "user")  # WORK-017 B-4
PICK_MARK_KEYS = ("failed", "same_list", "none")


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class ConsultPrompt:
    version: str
    sections: tuple[tuple[str, tuple[str, ...]], ...]
    system_text: str
    jev_front: dict[str, str]
    turn_headers: dict[str, str]
    tail_marker: str
    sha256: str
    steps: dict[str, str] = field(default_factory=dict)
    system_text_state: str | None = None  # 0.2 켠 상태의 시스템 글(새 절 셋이 있을 때만)
    jev_state: dict[str, str] = field(default_factory=dict)  # 0.2 `jev_state`(거둠 Noul 지시)
    slots: dict[str, str] = field(default_factory=dict)  # 0.3 W-10 — 빈 자리의 정해진 문구 값(선택 키 · LLM 에 안 감)
    system_text_v3: str | None = None  # 0.3 켠 상태(rev 3)의 시스템 글
    turn_kind: dict[str, str] = field(default_factory=dict)  # 0.3 `jev_state.turn_kind`
    kind_labels: dict[str, str] = field(default_factory=dict)  # 0.3 정보 종류 → 손님 말 이름
    voice_check: tuple[str, ...] = ()  # 0.3 금지 낱말 정규식(VO10)
    pick: dict | None = None  # 0.4 이해 호출 절(SPEC-008 §7 적재기 계약 — 한 칸이라도 없거나 비면 None)

    @property
    def draft(self) -> bool:
        return self.version.endswith("-draft")

    @property
    def state_ready(self) -> bool:
        """켠 대화(rev 4)를 시작할 수 있나(SPEC-008 §7 적재기 계약) — `system_text_v3` · `pick` · `turn_headers.consult` ·
        `kind_labels` 14 · `voice_check` · V3 머리 둘. `jev_state.retract` · `jev_front.target_option` · `turn_kind` 는 보지 않는다."""
        return bool(self.system_text_v3 and self.pick and self.turn_headers.get("consult")
                    and all(self.kind_labels.get(k) for k in KIND_LABEL_KEYS) and self.voice_check
                    and all(self.turn_headers.get(k) for k in V3_HEADER_KEYS))


def _pick(d: dict) -> dict | None:
    """0.4 `pick` — `version` · `rules[]`(1 줄 이상 · 빈 줄 아님) · `headers` · `marks` 가 다 있어야(아니면 None · 오류 아님)."""
    pk = d.get("pick")
    if not isinstance(pk, dict):
        return None

    def ok(v: object) -> bool:
        return isinstance(v, str) and bool(v.strip())
    rules, hd, mk = pk.get("rules"), pk.get("headers"), pk.get("marks")
    if not ok(pk.get("version")) or not isinstance(rules, list) or not rules or not all(ok(x) for x in rules):
        return None
    if not isinstance(hd, dict) or not all(ok(hd.get(k)) for k in (*PICK_HEADER_KEYS, *PICK_HEADER_EXTRA)):
        return None
    if not isinstance(mk, dict) or not all(ok(mk.get(k)) for k in PICK_MARK_KEYS):
        return None
    return {"version": pk["version"], "rules": list(rules), "headers": dict(hd), "marks": dict(mk)}


def _nonempty(v: object, where: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise PromptError(where)
    return v


def parse(raw: bytes) -> ConsultPrompt:
    try:
        d = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise PromptError("json") from e
    if not isinstance(d, dict) or any(k not in d for k in KEYS):
        raise PromptError("keys")
    version = _nonempty(d["version"], "version")
    system = d["system"]
    if not isinstance(system, list) or [s.get("id") if isinstance(s, dict) else None for s in system] != list(SECTION_IDS):
        raise PromptError("system.ids")
    sections = []
    for s in system:
        lines = s.get("lines")
        if not isinstance(lines, list) or not lines:
            raise PromptError(f"system.{s['id']}")
        sections.append((s["id"], tuple(_nonempty(x, f"system.{s['id']}.line") for x in lines)))
    jf = d["jev_front"]
    if not isinstance(jf, dict) or any(k not in jf for k in JEV_KEYS):
        raise PromptError("jev_front")
    jev = {k: _nonempty(jf[k], f"jev_front.{k}") for k in JEV_KEYS}
    for k in ("target_option", "target_abbr"):  # 0.2 · 0.3 검수 W-3 ⓑ — 있으면 빈 값 0
        if k in jf:
            jev[k] = _nonempty(jf[k], f"jev_front.{k}")
    th = d["turn_headers"]
    if not isinstance(th, dict) or any(k not in th for k in HEADER_KEYS):
        raise PromptError("turn_headers")
    headers = {k: _nonempty(th[k], f"turn_headers.{k}") for k in HEADER_KEYS}
    for k in ("consult", *V3_HEADER_KEYS):
        if k in th:
            headers[k] = _nonempty(th[k], f"turn_headers.{k}")
    mark = _nonempty(d["tail_marker"], "tail_marker")
    text = "\n\n".join("\n".join(lines) for _, lines in sections)
    if mark not in text:
        raise PromptError("tail_marker")
    text_state = None
    if "system_state" in d:  # 0.2 — 있으면 절 셋 · 순서 · 빈 값 0
        ss = d["system_state"]
        if not isinstance(ss, list) or [x.get("id") if isinstance(x, dict) else None for x in ss] != list(STATE_SECTION_IDS):
            raise PromptError("system_state.ids")
        extra = []
        for x in ss:
            lines = x.get("lines")
            if not isinstance(lines, list) or not lines:
                raise PromptError(f"system_state.{x['id']}")
            extra.append((x["id"], tuple(_nonempty(v, f"system_state.{x['id']}.line") for v in lines)))
        i = [sid for sid, _ in sections].index(STATE_AFTER) + 1
        merged = [*sections[:i], *extra, *sections[i:]]
        text_state = "\n\n".join("\n".join(lines) for _, lines in merged)
        if mark not in text_state:
            raise PromptError("tail_marker")
    jev_state: dict[str, str] = {}
    if "jev_state" in d:
        js = d["jev_state"]
        if not isinstance(js, dict) or any(k not in js for k in JEV_STATE_KEYS):
            raise PromptError("jev_state")
        jev_state = {k: _nonempty(js[k], f"jev_state.{k}") for k in JEV_STATE_KEYS}
    turn_kind: dict[str, str] = {}
    if "jev_state" in d and "turn_kind" in d["jev_state"]:  # 0.3 — 있으면 셋 다 · 빈 값 0
        tk = d["jev_state"]["turn_kind"]
        if not isinstance(tk, dict) or any(k not in tk for k in TURN_KIND_KEYS):
            raise PromptError("jev_state.turn_kind")
        turn_kind = {k: _nonempty(tk[k], f"jev_state.turn_kind.{k}") for k in TURN_KIND_KEYS}
    text_v3 = None
    if "system_v3" in d:  # 0.3 — 있으면 절 열셋 · 순서 · 빈 값 0 · 꼬리 표식이 글 안에
        sv = d["system_v3"]
        if not isinstance(sv, list) or [x.get("id") if isinstance(x, dict) else None for x in sv] != list(V3_SECTION_IDS):
            raise PromptError("system_v3.ids")
        v3 = []
        for x in sv:
            lines = x.get("lines")
            if not isinstance(lines, list) or not lines:
                raise PromptError(f"system_v3.{x['id']}")
            v3.append(tuple(_nonempty(v, f"system_v3.{x['id']}.line") for v in lines))
        text_v3 = "\n\n".join("\n".join(lines) for lines in v3)
        if mark not in text_v3:
            raise PromptError("tail_marker")
    kind_labels: dict[str, str] = {}
    if "kind_labels" in d:  # 0.3 — 있으면 14 종류 다 · 빈 값 0
        kl = d["kind_labels"]
        if not isinstance(kl, dict) or any(k not in kl for k in KIND_LABEL_KEYS):
            raise PromptError("kind_labels")
        kind_labels = {str(k): _nonempty(v, f"kind_labels.{k}") for k, v in kl.items()}  # 14 + 앱 쪽 이름(EM6 시술)
    voice: tuple[str, ...] = ()
    if "voice_check" in d:  # 0.3 — 있으면 정규식 목록(빈 값 · 깨진 식 0)
        vc = d["voice_check"]
        if not isinstance(vc, list) or not vc:
            raise PromptError("voice_check")
        voice = tuple(_nonempty(x, "voice_check.item") for x in vc)
        for x in voice:
            try:
                re.compile(x)
            except re.error as e:
                raise PromptError("voice_check.regex") from e
    steps: dict[str, str] = {}
    if "steps" in d:  # 있으면 넷 다 · 빈 값 0
        st = d["steps"]
        if not isinstance(st, dict) or any(k not in st for k in STEP_KEYS):
            raise PromptError("steps")
        steps = {k: _nonempty(st[k], f"steps.{k}") for k in STEP_KEYS}
    slots: dict[str, str] = {}
    if "slots" in d:  # 0.3 — 있으면 문자열 값 · 빈 값 0
        sl = d["slots"]
        if not isinstance(sl, dict):
            raise PromptError("slots")
        slots = {str(k): _nonempty(v, f"slots.{k}") for k, v in sl.items()}
    return ConsultPrompt(version, tuple(sections), text, jev, headers, mark, hashlib.sha256(raw).hexdigest(), steps,
                         text_state, jev_state, slots, system_text_v3=text_v3, turn_kind=turn_kind,
                         kind_labels=kind_labels, voice_check=voice, pick=_pick(d))


def load(path: Path) -> ConsultPrompt:
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise PromptError("file") from e
    return parse(raw)


def file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def keep(path: Path, chat_dir: Path) -> ConsultPrompt:
    """대화 시작 — 지금 파일을 읽어 보관본을 남긴다(이미 있으면 안 쓴다)."""
    raw = path.read_bytes()
    p = parse(raw)
    d = chat_dir / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{p.sha256}.json"
    if not f.exists():
        f.write_bytes(raw)
    return p


def kept(chat_dir: Path, sha: str) -> ConsultPrompt:
    """되살린 대화 — 보관본으로 돈다(LG4 · B-3)."""
    return load(chat_dir / "prompts" / f"{sha}.json")
