"""조각 그리기 — Jinja2(자동 이스케이프 켬 · EV4). 템플릿은 자리와 값만 갖는다(문구 0 · `data-slot`).

답 본문은 서버에서 마크다운으로 그린다(`md`) — 날 HTML 은 글자로 이스케이프(`html: False`) · 그림 · 자동 링크 끔 ·
링크는 markdown-it 의 주소 검사(javascript: 등 거절)를 거친다. 근거 꼬리는 그리기 전에 이미 떼어져 있다(tail.py).
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markdown_it import MarkdownIt
from markupsafe import Markup

TEMPLATES = Path(__file__).parent / "templates"

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True}).enable("table").disable("image")


def md(text: str | None) -> Markup:
    return Markup(_MD.render(text or ""))


class Render:
    def __init__(self, step_labels: dict[str, str] | None = None) -> None:
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True, trim_blocks=False,
                               lstrip_blocks=False)
        self.env.filters["md"] = md
        self.step_labels = dict(step_labels or {})

    def _t(self, name: str, **kw) -> str:
        return self.env.get_template(name).render(**kw).strip()

    def page(self, conv_id: str, turns: list[dict], lst: dict | None, sites: dict | None, *, fragment: bool) -> str:
        name = "partials/main.html" if fragment else "page.html"
        return self._t(name, conv_id=conv_id, turns=turns, lst=lst, sites=sites, show_codes=False)

    def face_check(self, sites: dict) -> str:
        return self._t("face_check.html", sites=sites, show_codes=True)

    def turn(self, conv_id: str, t: dict) -> str:
        return self._t("partials/turn.html", conv_id=conv_id, t=t)

    def rejected(self, kind: str, question: str = "") -> str:
        return self._t("partials/rejected.html", kind=kind, question=question, n="x")

    def step(self, code: str) -> str:
        """옛 꼴(단계 코드 하나) — 화면은 `steps` 를 쓴다."""
        return self._t("partials/step.html", code=code)

    def steps(self, items: list[dict]) -> str:
        return self._t("partials/steps.html", items=items, labels=self.step_labels)

    def delta(self, text: str) -> str:
        """지금까지 받은 답 글 전체(꼬리 뗀 것) → 답 자리 안쪽(마크다운)."""
        return self._t("partials/delta.html", text=text)

    def answer(self, n: int, body: str, end_state: str | None) -> str:
        return self._t("partials/answer.html", n=n, body=body, end_state=end_state)

    def error(self, n: int, kind: str) -> str:
        return self._t("partials/error.html", n=n, kind=kind)

    def list(self, lst: dict | None) -> str:
        return self._t("partials/list.html", lst=lst)

    def sites(self, sites: dict | None) -> str:
        return self._t("partials/sites.html", sites=sites, show_codes=False)
