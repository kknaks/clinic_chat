"""조각 그리기 — Jinja2(자동 이스케이프 켬 · EV4). 템플릿은 자리와 값만 갖는다(문구 0 · `data-slot`).

답 본문은 서버에서 마크다운으로 그린다(`md`) — 날 HTML 은 글자로 이스케이프(`html: False`) · 그림 · 자동 링크 끔 ·
링크는 markdown-it 의 주소 검사(javascript: 등 거절)를 거친다. 근거 꼬리는 그리기 전에 이미 떼어져 있다(tail.py).
"""

from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markdown_it import MarkdownIt
from markupsafe import Markup

TEMPLATES = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def asset(name: str) -> str:
    """테스트 고침 D(11-23) — 정적 파일 주소 + 판 표지 `?v=<파일 sha256 앞 10>`(파일이 바뀌면 주소가 바뀌어 브라우저가 새로
    받는다 · 사용자 화면에서 옛 CSS 로 스피너 두 줄 · 위급 상자 안 보임). 파일이 없으면 표지 없이."""
    p = STATIC_DIR / name
    if not p.is_file():
        return f"/static/{name}"
    return f"/static/{name}?v={_asset_hash(name, p.stat().st_mtime_ns)}"


@functools.lru_cache(maxsize=32)
def _asset_hash(name: str, mtime_ns: int) -> str:
    return hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:10]
UI_TEXT = Path(__file__).parent / "ui_text.json"  # 0.5 — 시안 글자(SPEC-010 §12 · 코드 · 템플릿 한글 0 — T17)


def ui_text() -> dict[str, str]:
    return {k: v for k, v in json.loads(UI_TEXT.read_text(encoding="utf-8")).items() if not k.startswith("_")}
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
                 slots: dict[str, str] | None = None, rec_max: int = 2) -> None:
        self.env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True, trim_blocks=False,
                               lstrip_blocks=False)
        self.env.filters["md"] = md
        self.env.globals["panel"] = panel  # 0.3 SV7 — 객체 패널 칸 · `state` 받는 자리
        self.env.globals["slots"] = dict(slots or {})  # 0.3 W-10 — 빈 자리의 정해진 문구 값(prompts/consult.json `slots`)
        self.env.globals["txt"] = ui_text()  # 0.5 — 시안 글자
        self.env.globals["rec_max"] = rec_max  # 0.5 CT4 — 추천 카드 자리 수(CHAT_REC_MAX)
        self.step_labels = dict(step_labels or {})
        self.env.globals["step_labels"] = self.step_labels
        self.env.globals["asset"] = asset  # 테스트 고침 D — 정적 파일 판 표지  # 테스트 고침 B — 도는 턴의 첫 단계 줄(turn.html)
        self.fig_rx = None  # 0.5 CH3 — 프롬프트 `kos.fig_marker` 줄 꼴(서버가 넣음)
        self.panel = panel

    def _t(self, name: str, **kw) -> str:
        return self.env.get_template(name).render(**kw).strip()

    def page(self, conv_id: str, turns: list[dict], lst: dict | None, sites: dict | None, *, fragment: bool,
             convs: list[dict] | None = None, states: list[dict] | None = None, outdated: bool = False,
             off_closed: bool = False, iv: dict | None = None, cart: list[dict] | None = None,
             rec: dict | None = None) -> str:
        name = "partials/main.html" if fragment else "page.html"
        return self._t(name, conv_id=conv_id, turns=turns, lst=lst, sites=sites, show_codes=False,
                       convs=convs or [], current=conv_id, states=states or [], outdated=outdated,
                       off_closed=off_closed, iv=iv, cart=cart or [], rec=rec)

    def items(self, iv: dict | None, conv_id: str | None = None) -> str:
        """0.5 `items` 이벤트 · GET /c/<id>/items — #procedure-list + #face-marks(oob)."""
        return self._t("partials/items.html", iv=iv, conv_id=conv_id, oob=True)

    def cart_body(self, cart: list[dict], rec: dict | None) -> str:
        """0.5 WP-5 — 장바구니 몸 조각(진료항목 · 옵션 줄 · 추천 카드)."""
        return self._t("partials/cart_body.html", cart=cart, rec=rec)

    def modal_body(self, pm: dict | None) -> str:
        """0.5 모달 안 조각(§4-5 · MD4)."""
        return self._t("partials/modal_body.html", pm=pm)

    def cart_item(self, ci: dict) -> str:
        """0.5 장바구니 진료항목 줄(OP2 ~ OP5 자리)."""
        return self._t("partials/cart_item.html", ci=ci)

    def drawer_list(self, convs: list[dict], current: str | None) -> str:
        return self._t("partials/drawer_list.html", convs=convs, current=current)

    def state_turn(self, st: dict) -> str:
        return self._t("partials/state_turn.html", st=st)

    def face_check(self, sites: dict, kos_sites: list[dict] | None = None) -> str:
        """`/face-check` — 옛 좌표표 점 + 0.5 `$J` 얼굴 점(FS6 · 나란히 비교)."""
        return self._t("face_check.html", sites=sites, show_codes=True, kos_sites=kos_sites or [])

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
        """지금까지 받은 답 글 전체(꼬리 뗀 것) → 답 자리 안쪽(마크다운) · 0.5 원리 그림 표지 줄은 흐르는 동안 숨김."""
        return self._t("partials/delta.html", text=self.fig_rx.sub("", text) if self.fig_rx else text)

    def split_fig(self, body: str, fig: dict | None) -> tuple[str, str]:
        """CH3 — 고른 그림 표지 줄 앞 · 뒤(다른 표지 줄은 지움 · 그림이 없으면 표지 줄 전부 지움)."""
        if not self.fig_rx:
            return body, ""
        if fig:
            for m in self.fig_rx.finditer(body or ""):
                if m.group(1) == fig.get("code"):
                    return self.fig_rx.sub("", body[: m.start()]), self.fig_rx.sub("", body[m.end():])
        return self.fig_rx.sub("", body or ""), ""

    def answer(self, n: int, body: str, end_state: str | None, fig: dict | None = None) -> str:
        before, after = self.split_fig(body, fig)
        return self._t("partials/answer.html", n=n, body=before, after=after, fig=fig if after or fig else None,
                       end_state=end_state)

    def error(self, n: int, kind: str) -> str:
        return self._t("partials/error.html", n=n, kind=kind)

    def list(self, lst: dict | None) -> str:
        return self._t("partials/list.html", lst=lst)

    def sites(self, sites: dict | None, conv_id: str | None = None) -> str:
        return self._t("partials/sites.html", sites=sites, show_codes=False, conv_id=conv_id)
