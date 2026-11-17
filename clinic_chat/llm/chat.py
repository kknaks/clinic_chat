"""대화 LLM 인터페이스(SPEC-004 §3-5 · §3-6 SD1 ~ SD5) — 한 턴 흐름은 이것 하나만 부른다.

CLI 구현(`chat_cli.py`)만 이 판에서 짓는다. SDK 구현은 같은 `turn()` 꼴로 갈아 끼운다(`history` 를 `messages` 로).
세션 UUID(`session_id`)와 대화 id(`conv_id`)는 따로다(CL6).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal, Protocol

SessionState = Literal["new", "open", "broken"]


def new_session_id() -> str:
    return str(uuid.uuid4())


@dataclass
class ChatConv:
    """대화 하나의 세션 상태(§3-4) — 장부가 들고 실행기가 옮긴다."""

    conv_id: str
    session_id: str = field(default_factory=new_session_id)
    state: SessionState = "new"
    running: bool = False  # CL12 ① — 한 대화에 한 번에 한 턴


def new_chat_result() -> dict:
    """§3-5 결과 꼴."""
    return {"answer_raw": "", "error": None, "tokens": {}, "observed_model": None, "observed_models": None,
            "thinking_seen": False, "stop_reason": None, "timing": {"ttft_ms": None, "total_ms": None},
            "llm": {"kind": None, "session_id": None, "mode": None, "model": None, "effort": None,
                    "cli_version": None, "returncode": None}}


def chat_error(kind: str, stage: str, detail: str | None = None) -> dict:
    return {"kind": kind, "stage": stage, "detail": detail}


class ChatLLM(Protocol):
    async def turn(self, conv: ChatConv, user_message: str, prompt: object, history: list[dict]) -> dict:
        """`prompt` = 대화 시작 때 읽은 상담 프롬프트(`consult.prompt.ConsultPrompt`) · `history` = 앞 턴의
        `{user_message, answer_raw}` 목록(CLI 구현은 쓰지 않는다 — 세션이 들고 있다 · SD2)."""
        ...
