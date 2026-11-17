"""상담 프롬프트 — `prompts/consult.json` 한 파일(WORK-011 §3 · `_RESUME` §2 11-17 「상담 프롬프트 = JSON 으로 불러옴」).

- 꼴 검사(키 · 절 `id` 순서 · 빈 값 0 · `tail_marker` 가 이어 붙인 글 안에) — 틀리면 `PromptError`(시작 실패).
- 이어 붙이기 = 절 안은 줄바꿈 하나 · 절 사이는 빈 줄 하나. sha = 파일 바이트의 sha256(JSON 전체 하나).
- 대화는 시작할 때 읽은 프롬프트로 끝까지 돈다 — 시작 때 `CHAT_DIR/prompts/<sha>.json` 으로 보관(있으면 안 쓴다).
- 0.2(WORK-012 §9a · 코디 W-7): `system` 다섯 절은 그대로 = 끈 상태의 시스템 글(0.1 과 글자 같음). 새 키는 **있으면** 꼴을
  검사한다 — `system_state`(절 `consult` · `ask` · `synergy` 이 순서) · `jev_front.target_option` · `jev_state.retract` ·
  `turn_headers.consult`. 켠 상태의 시스템 글 = role · rules · screen · consult · ask · synergy · material · tail.
  새 키가 다 있어야 `state_ready`(켠 대화의 시작 조건 — 아니면 서버가 오류 종류 `prompt` 로 받지 않는다).
"""

from __future__ import annotations

import hashlib
import json
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

    @property
    def draft(self) -> bool:
        return self.version.endswith("-draft")

    @property
    def state_ready(self) -> bool:
        """켠 대화를 시작할 수 있나 — 0.2 키가 전부 있다."""
        return bool(self.system_text_state and self.jev_state.get("retract") and self.jev_front.get("target_option")
                    and self.turn_headers.get("consult"))


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
    if "target_option" in jf:  # 0.2 — 있으면 빈 값 0
        jev["target_option"] = _nonempty(jf["target_option"], "jev_front.target_option")
    th = d["turn_headers"]
    if not isinstance(th, dict) or any(k not in th for k in HEADER_KEYS):
        raise PromptError("turn_headers")
    headers = {k: _nonempty(th[k], f"turn_headers.{k}") for k in HEADER_KEYS}
    if "consult" in th:
        headers["consult"] = _nonempty(th["consult"], "turn_headers.consult")
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
    steps: dict[str, str] = {}
    if "steps" in d:  # 있으면 넷 다 · 빈 값 0
        st = d["steps"]
        if not isinstance(st, dict) or any(k not in st for k in STEP_KEYS):
            raise PromptError("steps")
        steps = {k: _nonempty(st[k], f"steps.{k}") for k in STEP_KEYS}
    return ConsultPrompt(version, tuple(sections), text, jev, headers, mark, hashlib.sha256(raw).hexdigest(), steps,
                         text_state, jev_state)


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
