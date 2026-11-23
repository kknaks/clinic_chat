"""화면 단계 줄(수정 1 · 4번) — 걷기 · 실행기가 내는 단계 코드를 화면 단계 넷으로 옮긴다.

화면 단계 = `understand` · `search` · `list` · `answer`(문구는 `prompts/consult.json` 의 `steps` — 앱 코드에 문장 0).
진행 중 단계 하나 = 스피너 + 문구 · 끝난 단계 = 체크. `list` 는 목록을 보낸 순간 끝난 단계로 붙는다.
단계가 바뀔 때마다 줄 전체를 다시 그려 `step` 이벤트로 보낸다(다시 붙기에도 끝 꼴이 같다).
"""

from __future__ import annotations

KEYS = ("understand", "search", "list", "answer")
# 걷기(col_b.walk) · 실행기(chat_cli) 단계 코드 → 화면 단계
RAW = {"front": "understand", "hub": "search", "depth": "search", "gate": "search",
       "llm_wait": "answer", "llm": "answer"}


class Steps:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.ended = False  # gui-feedback F5 — 끝난 턴은 단계 줄 0

    def _close_active(self) -> None:
        for it in self.items:
            if it["state"] == "active":
                it["state"] = "done"

    def _add(self, key: str, state: str) -> bool:
        if any(it["key"] == key for it in self.items):
            return False
        self._close_active()
        self.items.append({"key": key, "state": state})
        return True

    def raw(self, code: str) -> bool:
        """걷기 · 실행기 단계 코드 — 화면 단계가 바뀌었으면 True."""
        key = RAW.get(code)
        return self._add(key, "active") if key else False

    def listed(self) -> bool:
        return self._add("list", "done")

    def finish(self) -> bool:
        changed = any(it["state"] == "active" for it in self.items) or not self.ended
        self._close_active()
        self.ended = True
        return changed

    def view(self) -> list[dict]:
        out = [dict(it) for it in self.items]
        if out and self.ended:
            out[-1]["end"] = True
        return out
