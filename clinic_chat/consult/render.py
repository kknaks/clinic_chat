"""조각 그리기 — Jinja2(자동 이스케이프 켬 · EV4). 템플릿은 자리와 값만 갖는다(문구 0 · `data-slot`).

답 본문은 서버에서 마크다운으로 그린다(`md`) — 날 HTML 은 글자로 이스케이프(`html: False`) · 그림 · 자동 링크 끔 ·
링크는 markdown-it 의 주소 검사(javascript: 등 거절)를 거친다. 근거 꼬리는 그리기 전에 이미 떼어져 있다(tail.py).
"""

from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markdown_it import MarkdownIt
from markupsafe import Markup

TEMPLATES = Path(__file__).parent / "templates"
STATE_KEYS = ("after", "diff", "blanks", "ask", "turn_kind", "close")  # 0.3 SV8 · SV9 — 패널에 그리는 장부 `consult.*` 칸

_MD = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True}).enable("table").disable("image")


def md(text: str | None) -> Markup:
    return Markup(_MD.render(text or ""))


def state_view(row: dict, *, open_: bool = False) -> dict:
    """0.3 SV8 · SV9 — 한 턴 장부 줄 → 패널 묶음(값은 장부 글자 그대로 · 없는 칸은 안 그림 · 끈 상태 · 오류 턴은 빈 묶음)."""
    c = row.get("consult") or {}
    ok = row.get("error") is None and c.get("after") is not None
    fields = [(k, json.dumps(c[k], ensure_ascii=False, indent=1)) for k in STATE_KEYS if ok and k in c]
    view = [{"code": x.get("code"), "name": x.get("name"), "state": x.get("state")}
            for x in ((c.get("after") or {}).get("concerns") or []) if (x.get("source") or {}).get("how") == "view"]
    if ok and view:  # 검수 W-5 — 누르기로 든 고민(`how=view`)을 손님이 말한 것과 갈라 보인다
        fields.append(("how=view", json.dumps(view, ensure_ascii=False, indent=1)))
    if ok and c.get("viewing"):
        fields.append(("viewing", json.dumps(c["viewing"], ensure_ascii=False, indent=1)))
    if ok and c.get("understand") is not None:  # 0.4 §6-2 — 무엇으로 읽었나 · 버렸나 · 얼마나 걸렸나(칸 이름 = 장부 키)
        u = {k: c["understand"].get(k) for k in ("accepted", "dropped", "merged", "ms", "error")}
        fields.append(("understand", json.dumps(u, ensure_ascii=False, indent=1)))
    if ok and c.get("site_filter") is not None:
        fields.append(("site_filter", json.dumps(c["site_filter"], ensure_ascii=False, indent=1)))
    return {"turn": row.get("turn"), "fields": fields, "open": open_}


class Render:
    def __init__(self, step_labels: dict[str, str] | None = None, *, panel: bool = False,
                 slots: dict[str, str] | None = None) -> None:
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True, trim_blocks=False,
                               lstrip_blocks=False)
        self.env.filters["md"] = md
        self.env.globals["panel"] = panel  # 0.3 SV7 — 객체 패널 칸 · `state` 받는 자리
        self.env.globals["slots"] = dict(slots or {})  # 0.3 W-10 — 빈 자리의 정해진 문구 값(prompts/consult.json `slots`)
        self.step_labels = dict(step_labels or {})
        self.panel = panel

    def _t(self, name: str, **kw) -> str:
        return self.env.get_template(name).render(**kw).strip()

    def page(self, conv_id: str, turns: list[dict], lst: dict | None, sites: dict | None, *, fragment: bool,
             convs: list[dict] | None = None, states: list[dict] | None = None, outdated: bool = False,
             off_closed: bool = False) -> str:
        name = "partials/main.html" if fragment else "page.html"
        return self._t(name, conv_id=conv_id, turns=turns, lst=lst, sites=sites, show_codes=False,
                       convs=convs or [], current=conv_id, states=states or [], outdated=outdated,
                       off_closed=off_closed)

    def drawer_list(self, convs: list[dict], current: str | None) -> str:
        return self._t("partials/drawer_list.html", convs=convs, current=current)

    def state_turn(self, st: dict) -> str:
        return self._t("partials/state_turn.html", st=st)

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

    def sites(self, sites: dict | None, conv_id: str | None = None) -> str:
        return self._t("partials/sites.html", sites=sites, show_codes=False, conv_id=conv_id)
