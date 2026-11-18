"""진행 알림 — runner · 열 · CLI 가 걷는 중에 내는 이벤트를 받는 곳(WORK-003 SSE).

받는 곳(`sink`)이 없으면 아무 일도 하지 않는다(CLI · batch 는 그대로). 화면 서버는 `listen(sink)` 안에서
`runner.run_question` 을 부른다 — 이 모듈은 값을 옮기기만 하고 걷기 · 답 · 판정에 끼지 않는다.

이벤트: `start{column}` · `delta{column, text}`(CLI stream-json 부분 메시지의 text_delta) ·
`interim{column, text}`(도구 호출이 왔다 — 그때까지 답 칸에 흐른 글 `text` 는 메모로 옮긴다) ·
`step{column, step}`(B 단계 ① ~ ⑤) · `column_done{column, result, model, effort}`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import Any

Sink = Callable[[dict], None]

_sink: ContextVar[Sink | None] = ContextVar("clinic_chat_progress_sink", default=None)
_column: ContextVar[str | None] = ContextVar("clinic_chat_progress_column", default=None)


@contextlib.contextmanager
def listen(sink: Sink) -> Iterator[None]:
    tok = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(tok)


@contextlib.contextmanager
def tap(fn: Sink) -> Iterator[None]:
    """이 안의 이벤트를 `fn` 이 먼저 보고(값을 얹을 수 있다) 바깥 받는 곳으로 넘긴다 — 바깥이 없어도 `fn` 은 본다."""
    outer = _sink.get()

    def both(ev: dict) -> None:
        try:
            fn(ev)
        finally:
            if outer is not None:
                outer(ev)

    tok = _sink.set(both)
    try:
        yield
    finally:
        _sink.reset(tok)


@contextlib.contextmanager
def gate(fn: Callable[[dict], dict | None]) -> Iterator[None]:
    """이 안의 이벤트를 `fn` 이 고쳐(또는 None 으로 막아) 바깥 받는 곳으로 넘긴다 — B 답 꼬리를 화면에 안 흘릴 때."""
    outer = _sink.get()
    if outer is None:
        yield
        return

    def pass_(ev: dict) -> None:
        got = fn(ev)
        if got is not None:
            outer(got)

    tok = _sink.set(pass_)
    try:
        yield
    finally:
        _sink.reset(tok)


@contextlib.contextmanager
def column(name: str) -> Iterator[None]:
    """이 안에서 나는 delta · step 은 이 열의 것이다."""
    tok = _column.set(name)
    try:
        yield
    finally:
        _column.reset(tok)


def emit(kind: str, **data: Any) -> None:
    s = _sink.get()
    if s is None:
        return
    data.setdefault("column", _column.get())
    try:
        s({"event": kind, **data})
    except Exception:  # noqa: BLE001 — 알림이 걷기를 멈추게 하지 않는다
        pass
