"""조건 칸 · 주제 = 칸 묶음(튜플)(SPEC-010 §5-0 CD1 ~ CD6 · 사용자 결정 11-22 「주제 = 칸 묶음」 — 옛 TP1 ~ TP14 판단 대체).

조건 칸 = `{procedure: [v], site: [v], concern: [v], effect: [v], product: [v], country: str | None, exclude: [v]}` · 값 v =
`{table, code, name}`(이름 일치는 table `name` · `items` 를 함께 든다). 주제 = `{id, conditions, items, created_turn, sites}`.

판단(위에서 처음 맞는 것 · 결정론 — 진료항목 셈 `count` 만 밖에서):
1. 이번 턴 칸 0 · 또는 정보 질문(`info` — 부위 · 고민 없이 지금 목록 안 것을 묻는 말) → 주제 그대로(조건 안 바뀜).
1b. (O-14) 이번 턴 부위 · 고민 · 시술 값을 **그대로 가진 앞 주제**가 있고 지금 주제는 그 값을 다 갖지 않음 → 그 주제로 되돌림
   (하나면 규칙 · 둘 이상이면 LLM `to` · 없으면 물음) — 지금 주제의 빈 칸보다 앞선다.
2. 지금 주제와 부딪힘 없음 → **깊게**(빈 칸 채움 · 좁힘 칸 교체 · 뺌 칸 더함).
3. 부딪히고 앞 주제 하나와 부딪힘 없음 → 그 주제로 **되돌림**(저장 상태 + 빈 칸 채움).
4. 앞 주제 둘 이상과 맞음 → 이해 호출 `topic.to`(그 가운데) · 없거나 무효면 **물음**(조건 안 바꿈 · `ask`).
5. 어느 주제와도 부딪힘 → **새 주제**(이번 턴 칸만).
부딪힘 칸 = 부위 · 고민 · 시술(둘 다 값이 있고 이번 턴 값이 그 주제 값 밖) · 좁힘 칸 = 효과 · 제품 · 국가 · 뺌(새 주제 아님).
이해 호출 판단은 참고(장부 `llm_agree`) — 4 만 LLM `to` 를 쓴다. 주제끼리 칸은 섞이지 않는다.

11-23 사용자 결정(「업데이트는 좁히기만이 아니라 넓히기 · 되돌리기도」):
- 부딪힘 칸 = 부위 · 고민 **만**. 시술 · 제품은 「고르기」 묶음 — 같은 주제 안에서 새 값이 오면 **둘 다 교체**(좁힘 누적 아님 ·
  「팔자주름 · 써마지」 + 「보톡스」 → 「팔자주름 · 보톡스」). 교체로 0 이면 막지 않고 따른다(`rec.pick_zero` — 봇이 0 이라고 말함).
- 0. 바로잡기(`undo` — 표지 낱말 또는 이해 `topic.move = undo`): 직전 턴에 바뀐 주제를 그 턴 전 상태로 되돌린 뒤(새로 만든 주제면
  지움) 이번 말을 위 판단대로 적용.
- 0b. 넓히기(`widen` — 표지 낱말 또는 `topic.move = widen`): 지금 주제의 좁힘 칸(시술 · 제품 · 효과 · 국가 · 뺌)을 비운 뒤 적용.
- 주제마다 `last{turn, conditions, items, created}` = 그 턴에 바뀌기 전 상태(되돌리기 재료 · 장부 `consult.topics` 에 함께).
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from clinic_chat.consult.detect import SLOT_OF_TABLE, SLOTS, NameHit

CONFLICT = ("site", "concern")  # 부딪힘 칸(11-23 — 시술은 고르기 묶음으로)
PICK = ("procedure", "product")  # 고르기 묶음 — 이번 턴에 둘 중 하나라도 오면 둘 다 교체
NARROW = ("effect",)  # 좁힘 칸(교체) — 고르기 · 국가 · 뺌도 좁힘
# 11-23 표지 낱말(공백 지운 말 · 장부 `topic.undo_marker` · `widen_marker`) — 목록은 사용자 확인 대상
UNDO_WORDS = (
    "\uc544\ub2c8",  # 아니(말머리 — 아래 undo_marker)
    "\uadf8\uac70\ub9d0\uace0",  # 그거말고
    "\uadf8\uac8c\uc544\ub2c8\ub77c",  # 그게아니라
    "\uc798\ubabb",  # 잘못
)
MALGO = "\ub9d0\uace0"  # 「~ 말고」 — 뺌(exclude)이 없을 때만 바로잡기
WIDEN_WORDS = (
    "\ub2e4\ub978\uc2dc\uc220",  # 다른시술
    "\ub2e4\ub978\uac83",  # 다른것
    "\ub2e4\ub978\uac70",  # 다른거
    "\uc804\uccb4\ub85c",  # 전체로
    "\uc804\ubd80",  # 전부
    "\uc0c1\uad00\uc5c6\uc774",  # 상관없이
)
EXCLUDE_TABLES = ("procedure", "modality", "procedure_case", "product")  # 뺌 칸에 둘 수 있는 표
# O-15(11-22 확정) 정보 질문 — 이해 `kinds`(원리 · 효과 · 부작용 …) · `out` 에 더해 가격 낱말(종류 코드에 가격이 없음 · 앱 표지)
PRICE_WORDS = ("\uac00\uaca9", "\uc5bc\ub9c8", "\ube44\uc6a9")  # 「가격」 · 「얼마」 · 「비용」


def undo_marker(utterance: str, has_exclude: bool = False) -> str | None:
    """\ubc14\ub85c\uc7a1\uae30 \ud45c\uc9c0 \u2014 \ub9d0\uba38\ub9ac \u300c\uc544\ub2c8\u300d \u00b7 \u300c\uadf8\uac70 \ub9d0\uace0\u300d \u00b7 \u300c\uadf8\uac8c \uc544\ub2c8\ub77c\u300d \u00b7 \u300c\uc798\ubabb\u300d \u00b7 \ube8c \uc5c6\ub294 \u300c~ \ub9d0\uace0\u300d."""
    c = "".join((utterance or "").split())
    if c.startswith(UNDO_WORDS[0]) and not c.startswith("\uc544\ub2c8\uc694"):  # 「아니요」 대답 빼고
        return UNDO_WORDS[0]
    for w in UNDO_WORDS[1:]:
        if w in c:
            return w
    if MALGO in c and not has_exclude:
        return MALGO
    return None


def widen_marker(utterance: str) -> str | None:
    c = "".join((utterance or "").split())
    return next((w for w in WIDEN_WORDS if w in c), None)


def info_question(kinds: list[str] | None, out: bool, utterance: str) -> bool:
    """O-15 \u2014 \uc815\ubcf4 \uc9c8\ubb38(\uc8fc\uc81c \u00b7 \uc870\uac74 \uadf8\ub300\ub85c \ud6c4\ubcf4): \uc815\ubcf4 \uc885\ub958\uac00 \uc788\uac70\ub098 \u00b7 \ub2f5\ud560 \uc218 \uc5c6\ub294 \ubb3c\uc74c\uc774\uac70\ub098 \u00b7 \uac00\uaca9\uc744 \ubb3b\ub294 \ub9d0."""
    c = "".join((utterance or "").split())
    return bool(kinds) or bool(out) or any(w in c for w in PRICE_WORDS)


def empty() -> dict:
    return {**{s: [] for s in SLOTS}, "country": None, "exclude": []}


def _key(v: dict) -> tuple[str, str]:
    return (str(v.get("table")), str(v.get("code")))


def is_empty(c: dict | None) -> bool:
    return not c or (not any(c.get(s) for s in SLOTS) and not c.get("country") and not c.get("exclude"))


def active_conds(c: dict | None) -> dict:
    """SPEC-012 HP1 — 셈에 쓰는 조건: `suspended` 칸(효과)은 칸에 남기되 셈에서만 쉰다."""
    out = copy.deepcopy(c) if c else empty()
    for s in out.pop("suspended", None) or []:
        out[s] = []
    return out


def suspend_aware(count: Count) -> Count:
    """셈 함수 감쌈 — 쉬는 칸을 빼고 센다(모든 판단 길이 같은 셈을 쓰게)."""

    async def c(conds: dict) -> list[str]:
        return await count(active_conds(conds))

    c.__wrapped_count__ = True  # type: ignore[attr-defined]
    return c


def turn_values(turn: dict) -> set[tuple[str, str]]:
    """이번 턴 칸 값(뺌 제외) — HU1 사진 견줌."""
    return {_key(v) for s in SLOTS for v in turn.get(s) or [] if v.get("table") != "name"}


def snap_values(last: dict) -> set[tuple[str, str]]:
    return {_key(v) for s in SLOTS for v in (last.get("conditions") or {}).get(s) or []}


def turn_conditions(
    targets: list[dict],
    hits: list[NameHit],
    country: str | None,
    *,
    follow: dict | None = None,
    exclude: list[dict] | None = None,
) -> tuple[dict, list[str]]:
    """이번 턴 조건 — 0.4 `kind = follow` 로 채운 대상은 뺀다(§5-0b 「이번 턴 조건」 · T18) · method · material 은 칸 아님(CD5).
    둘째 값 = 칸이 바뀐 차례(targets 순서 · LS2 역순 셈용 · 같은 칸은 마지막 자리)."""
    c = empty()
    order: list[str] = []
    for t in targets:
        if follow and (t.get("table"), t.get("code")) == (follow.get("table"), follow.get("code")):
            continue
        slot = SLOT_OF_TABLE.get(str(t.get("table")))
        if slot is None:
            continue
        v = {"table": t["table"], "code": t["code"], "name": t.get("name") or ""}
        if _key(v) not in {_key(x) for x in c[slot]}:
            c[slot].append(v)
        if slot in order:
            order.remove(slot)
        order.append(slot)
    for h in hits:  # NM7 — 이름 일치는 허브 대상과 별개로 시술 칸에
        v = {"table": "name", "code": f"{h.kind}:{h.name}", "name": h.name, "items": list(h.items)}
        if _key(v) not in {_key(x) for x in c["procedure"]}:
            c["procedure"].append(v)
        if "procedure" in order:
            order.remove("procedure")
        order.append("procedure")
    if country:
        c["country"] = country
        order.append("country")
    for x in exclude or []:  # 「보톡스 말고」 — 뺌 칸(시술 · 계열 · 케이스 · 제품 표만)
        if x.get("table") not in EXCLUDE_TABLES:
            continue
        v = {"table": x["table"], "code": x["code"], "name": x.get("name") or ""}
        if _key(v) not in {_key(y) for y in c["exclude"]}:
            c["exclude"].append(v)
    if c["exclude"]:
        order.append("exclude")
    return c, order


def merge(base: dict | None, turn: dict) -> dict:
    """깊게 — 부딪힘 칸은 빈 칸만 채움(값이 있으면 그대로) · 좁힘 칸(효과 · 제품 · 국가)은 교체 · 뺌은 더함(이번 턴에 다시
    말한 값은 뺌에서 지움)."""
    out = copy.deepcopy(base) if base else empty()
    out.setdefault("exclude", [])
    for s in CONFLICT:
        if turn.get(s) and not out.get(s):
            out[s] = copy.deepcopy(turn[s])
    if any(turn.get(s) for s in PICK):  # 11-23 — 고르기 묶음은 둘 다 교체
        for s in PICK:
            out[s] = copy.deepcopy(turn.get(s) or [])
    for s in NARROW:
        if turn.get(s):
            out[s] = copy.deepcopy(turn[s])
            if s in (out.get("suspended") or []):  # 새 효과를 말하면 쉼 풂(SPEC-012 HP1)
                out["suspended"] = [x for x in out["suspended"] if x != s]
                if not out["suspended"]:
                    out.pop("suspended")
    if turn.get("country"):
        out["country"] = turn["country"]
    said = {_key(v) for s in SLOTS for v in turn.get(s) or []}
    ex = [v for v in out["exclude"] if _key(v) not in said]
    for v in turn.get("exclude") or []:
        if _key(v) not in {_key(x) for x in ex}:
            ex.append(copy.deepcopy(v))
    out["exclude"] = ex
    return out


def _same_value(v: dict, vals: list[dict]) -> bool:
    """\uac19\uc740 \uac12 \u2014 (\ud45c, \ucf54\ub4dc)\uac00 \uac19\uac70\ub098 \uc6d0\ubb38 \uc774\ub984(\uacf5\ubc31 \uc9c0\uc6c0)\uc774 \uac19\ub2e4(\u300c\ubcf4\ud1a1\uc2a4\u300d = \uacc4\uc5f4 M1 \u00b7 \uc2dc\uc220 PRC-001 \u2014 \uc2e4\ubb3c a7d7e253 9 \ud134)."""
    nm = "".join(str(v.get("name") or "").split())
    return any(_key(v) == _key(x) or (nm and nm == "".join(str(x.get("name") or "").split())) for x in vals)


def holds(turn: dict, conds: dict | None) -> bool:
    """O-14 — 이번 턴 부딪힘 칸(부위 · 고민 · 시술) 값이 하나 이상 있고, 그 값이 모두 그 주제 칸에 같은 값으로 있다(이름 일치 값은
    셈 밖)."""
    c = conds or {}
    vals = [(s, v) for s in CONFLICT for v in turn.get(s) or [] if v.get("table") != "name"]
    return bool(vals) and all(_same_value(v, [x for x in c.get(s) or [] if x.get("table") != "name"]) for s, v in vals)


def conflicts(turn: dict, conds: dict | None) -> list[str]:
    """부딪힌 칸 — 부위 · 고민 · 시술 칸에 둘 다 값이 있고 이번 턴 값이 그 주제 값 밖에 있으면. 이름 일치 값(table `name` —
    KOS 이름 글자 맞춤)은 부딪힘을 만들지 않는다(그 말 안 KOS 이름은 좁힘 재료)."""
    c = conds or {}
    out = []
    for s in CONFLICT:
        tv = [v for v in turn.get(s) or [] if v.get("table") != "name"]
        cv = [v for v in c.get(s) or [] if v.get("table") != "name"]
        if tv and cv and not all(_same_value(v, cv) for v in tv):
            out.append(s)
    return out


def withdraw(conds: dict, gone: list[str]) -> tuple[dict, bool]:
    """CD4 \u2014 \uac70\ub454 \ub300\uc0c1(`table:code`)\uc758 \uce78 \uac12\uc744 \uc9c0\uc6b4\ub2e4."""
    g = {tuple(x.split(":", 1)) for x in gone if ":" in x}
    out = copy.deepcopy(conds)
    changed = False
    for s in SLOTS:
        keep = [v for v in out.get(s) or [] if _key(v) not in g]
        changed |= len(keep) != len(out.get(s) or [])
        out[s] = keep
    return out, changed


@dataclass
class Decision:
    topics: list[dict]
    active: str | None
    rec: dict  # 장부 `consult.topic`(TP10 한 정의)
    blocked: dict | None = None  # LS2
    ls: str | None = None  # LS1 · LS3
    changed: bool = False  # 목록 · 주제가 바뀌었나(화면 `items` 조각)
    results: dict = field(default_factory=dict)  # 셈 결과(장부 · 화면)
    ask: list[str] | None = None  # 앞 주제 둘 이상과 맞고 LLM `to` 가 없음 → 봇이 물을 주제 id


Count = Callable[[dict], Awaitable[list[str]]]


def _find(topics: list[dict], tid: str | None) -> dict | None:
    return next((t for t in topics if str(t.get("id")) == str(tid)), None) if tid is not None else None


def _new_id(topics: list[dict]) -> str:
    n = 1
    ids = {str(t.get("id")) for t in topics}
    while f"t{n}" in ids:
        n += 1
    return f"t{n}"


async def decide(
    topics: list[dict],
    active: str | None,
    turn: dict,
    order: list[str],
    *,
    n: int,
    llm: dict | None,
    withdrawn: list[str],
    count: Count,
    max_n: int | None = None,
    info: bool = False,
    utterance: str = "",
    question: bool = False,
) -> Decision:
    """\uc8fc\uc81c = \uce78 \ubb36\uc74c \ud310\ub2e8(\ubaa8\ub4c8 \uba38\ub9ac 1 ~ 5) \u00b7 CD4 \uac70\ub460 \u00b7 \uae4a\uac8c\uac00 0 \uc774\uba74 LS1 ~ LS3(\ub9c9\uc740 \uce78)."""
    topics = copy.deepcopy(topics)
    for t in topics:
        t.setdefault("conditions", empty()).setdefault("exclude", [])
    cur = _find(topics, active) or (topics[-1] if topics else None)
    count = suspend_aware(count)  # SPEC-012 HP1 — 쉬는 칸(효과)은 셈에서만 뺌
    llm_m = (llm or {}).get("move")
    undo_mk = undo_marker(utterance, bool(turn.get("exclude")))
    widen_mk = widen_marker(utterance)
    rec: dict = {
        "change": False,
        "by": None,
        "move": None,  # same · deepen · return · ask · new · first
        "conflict": [],
        "candidates": [],
        "llm_move": (llm or {}).get("move"),
        "llm_to": (llm or {}).get("to"),
        "to_from": None,
        "llm_agree": None,
        "info": False,
        "same_values": False,  # O-14 — 같은 값 앞 주제로 되돌림
        "undo": None,  # 11-23 바로잡기 {from: rule|llm, marker, topic, removed}
        "widen": None,  # 11-23 넓히기 {from, marker, topic, cleared[]}
        "pick_zero": False,  # 11-23 고르기 교체로 0(막지 않음)
        "question": bool(question),  # SPEC-012 HQ0 — 끝 「?」 + 1차 kinds(부르는 쪽이 셈)
        "asked_pick": None,  # HQ1 — 물음 턴의 고르기 · 뺌(답 재료로만 · 칸에 안 넣음)
        "out_pick": None,  # HQ2 — 명동점 진료항목 0 인 고르기(앞 목록 그대로)
        "list_held": None,  # HL1 — 막은 칸 때문에 앞 목록을 보이는 중
        "suspended": None,  # HP1 — 이 턴 셈에서 쉬게 한 칸
        "A": None,
    }
    dec = Decision(topics, cur["id"] if cur else None, rec)
    # 0 — 바로잡기: 직전 턴(n-1)에 바뀐 주제를 그 전 상태로
    if undo_mk or llm_m == "undo":
        rec["undo"] = {"from": "rule" if undo_mk else "llm", "marker": undo_mk, "topic": None, "removed": False}
        for t in list(topics):
            last = t.get("last") or {}
            if last.get("turn") != n - 1:
                continue
            rec["undo"]["topic"] = str(t["id"])
            new_vals = turn_values(turn) - snap_values(last)
            # SPEC-012 HU1 — 사진에 없는 새 값이 고르기(시술 · 제품) 칸에만 있으면 교체(되돌리지 않음 · 부위 · 고민 남음).
            # 새 값이 부위 · 고민 칸에도 있으면 직전 변화가 다른 주제로 간 것 → 지금처럼 되돌린 뒤 적용(실물 67bb T4 「아니 이마에 써마지」)
            new_conflict = {k for k in new_vals for sl in CONFLICT for v in turn.get(sl) or [] if _key(v) == k}
            if not last.get("created") and new_vals and not new_conflict:
                rec["undo"]["skipped"] = "new_value"
                rec["undo"]["new"] = sorted(f"{a}:{b}" for a, b in new_vals)
                continue
            if last.get("created"):
                topics.remove(t)
                rec["undo"]["removed"] = True
                if cur is t:
                    cur = max(topics, key=lambda x: x.get("created_turn") or 0) if topics else None
            else:
                t["conditions"], t["items"] = copy.deepcopy(last["conditions"]), list(last.get("items") or [])
                if last.get("list_held"):  # HL1 — 사진의 표지도 같이 되돌림
                    t["list_held"] = copy.deepcopy(last["list_held"])
                else:
                    t.pop("list_held", None)
                t.pop("last", None)
                rec["undo"]["restored"] = True
            dec.changed = True
        dec.active = cur["id"] if cur else None
        # SPEC-012 HU1 — 사진 복원 뒤 이번 턴 값이 모두 그 주제 칸에 있으면(「아니 아까 거」) 그대로 둔다(복원한 목록 · 표지 유지)
        rt = _find(topics, rec["undo"].get("topic")) if rec["undo"].get("restored") else None
        if rt is not None and rt is cur and turn_values(turn) <= {_key(v) for s in SLOTS for v in rt["conditions"].get(s) or []}:
            rec.update(move="undo_restore", change=True, by="undo")
            return dec
    # 0b — 넓히기: 지금 주제의 좁힘 칸을 비움(부위 · 고민 그대로)
    if cur is not None and (widen_mk or llm_m == "widen"):
        c0 = cur["conditions"]
        # SPEC-012 HW1 — 넓히기 = 시술 · 제품 · 국가 · 뺌만 비움 · 효과는 남기고 쉬던 효과는 되살림
        cleared = [s for s in (*PICK, "exclude") if c0.get(s)] + (["country"] if c0.get("country") else [])
        if c0.get("suspended"):
            cleared.append("suspended")
        rec["widen"] = {
            "from": "rule" if widen_mk else "llm",
            "marker": widen_mk,
            "topic": str(cur["id"]),
            "cleared": cleared,
        }
        if cleared:
            snap(cur, n)
            for s in (*PICK, "exclude"):
                c0[s] = []
            c0["country"] = None
            c0.pop("suspended", None)
            cur.pop("list_held", None)
            cur["items"] = await count(c0) if not is_empty(c0) else []
            dec.changed = True
    if cur is not None and withdrawn:  # CD4 — 거둠은 지금 주제 칸에서 지운다(목록은 다시 센다)
        cur["conditions"], wchanged = withdraw(cur["conditions"], withdrawn)
        if wchanged:
            cur["items"] = await count(cur["conditions"]) if not is_empty(cur["conditions"]) else []
            dec.changed = True
    prev = visible_prev(topics, cur, max_n)
    llm_move, llm_to = rec["llm_move"], rec["llm_to"]

    def agree(move: str) -> None:
        want = {"same": "continue", "deepen": "continue", "ask": "continue", "first": "continue"}.get(move, move)
        rec["move"] = move
        rec["llm_agree"] = None if llm_move in (None, "undo", "widen") else (llm_move == want)

    # SPEC-012 HQ2(데이터) — 고르기 값의 명동점 진료항목이 0(밖 · 없는 시술)이면 고르기를 적용하지 않는다(앞 목록 그대로)
    if cur is not None and any(turn.get(s) for s in PICK):
        pk = empty()
        for s in PICK:
            pk[s] = copy.deepcopy(turn.get(s) or [])
        if not await count(pk):
            rec["out_pick"] = [v.get("name") or v.get("code") for s in PICK for v in turn.get(s) or []
                               if v.get("from") != "product"]
            turn = copy.deepcopy(turn)
            for s in PICK:
                turn[s] = []
    # SPEC-012 HQ1 — 물음(끝 「?」 + 1차 kinds)이고 부위 · 고민 값이 모두 지금 주제 값과 같거나 없으면 그대로(고르기 · 뺌은 답 재료로만)
    if question and cur is not None:
        cc = cur.get("conditions") or {}
        same_sc = all(_same_value(v, cc.get(s) or []) for s in ("site", "concern") for v in turn.get(s) or []
                      if v.get("table") != "name")
        if same_sc:
            asked = [v.get("name") or v.get("code") for s in PICK for v in turn.get(s) or [] if v.get("from") != "product"]
            ex = [v.get("name") or v.get("code") for v in turn.get("exclude") or []]
            if asked or ex:
                rec["asked_pick"] = {"pick": asked, "exclude": ex}
            rec["info"] = True
            agree("same")
            return dec
    # 1 — 칸 0 · 정보 질문
    if is_empty(turn):
        agree("same")
        return dec
    if info and cur is not None and not turn.get("site") and not turn.get("concern"):
        got = await count(turn)
        if set(got) & set(cur.get("items") or []):
            rec["info"] = True
            agree("same")
            return dec
    if cur is None:  # 첫 주제
        cur = {"id": _new_id(topics), "conditions": empty(), "items": [], "created_turn": n}
        topics.append(cur)
        dec.active = cur["id"]
        rec.update(change=True, by="first")
        agree("first")
        return await _deepen(dec, cur, turn, order, count, created=True, n=n)
    rec["conflict"] = conflicts(turn, cur["conditions"])
    fit: list[dict] = []
    # 1b(O-14 · 사용자 결정 11-22) — 이번 턴 부딪힘 칸 값을 그대로 가진 앞 주제가 있으면 깊게보다 먼저 되돌림
    # (지금 주제가 이미 그 값을 가졌으면 지금 주제에 머무름)
    if not holds(turn, cur["conditions"]):
        fit = [t for t in prev if holds(turn, t["conditions"])]
        if fit:
            rec["same_values"] = True
    # 2 — 깊게
    if not fit and not rec["conflict"]:
        agree("deepen")
        return await _deepen(dec, cur, turn, order, count, n=n)
    # 3 · 4 — 부딪힘 없는 앞 주제
    if not fit:
        fit = [t for t in prev if not conflicts(turn, t["conditions"])]
    rec["candidates"] = [str(t["id"]) for t in fit]
    target = None
    if len(fit) == 1:
        target, rec["to_from"] = fit[0], "rule"
    elif len(fit) > 1:
        target = next((t for t in fit if str(t["id"]) == str(llm_to)), None)
        if target is not None:
            rec["to_from"] = "llm"
        else:
            dec.ask = rec["candidates"]
            agree("ask")
            return dec
    if target is not None:
        agree("return")
        rec.update(change=True, by="return")
        dec.active, dec.changed = target["id"], True
        m2 = merge(target["conditions"], turn)
        if m2 != target["conditions"]:
            got = await count(m2)
            if got:
                snap(target, n)
                target["conditions"], target["items"] = m2, got
            else:
                rec["add_zero"] = True  # 채우면 0 — 저장 상태 그대로
        return dec
    # 5 — 새 주제(이번 턴 칸만)
    agree("new")
    b_codes = await count(turn)
    rec["A"] = len(b_codes)
    if not b_codes:
        dec.blocked = {"slot": "all", "slots": list(dict.fromkeys(order)), "without_n": None}
        dec.ls = "LS1" if cur.get("items") else "LS3"
        rec["move"] = "new_zero"
        return dec
    t = {
        "id": _new_id(topics),
        "conditions": copy.deepcopy(turn),
        "items": list(b_codes),
        "created_turn": n,
        "last": {"turn": n, "created": True},
    }
    topics.append(t)
    dec.active, dec.changed = t["id"], True
    rec.update(change=True, by="new")
    return dec


def snap(t: dict, n: int) -> None:
    """\ub418\ub3cc\ub9ac\uae30 \uc7ac\ub8cc \u2014 \uc774 \ud134\uc5d0 \ucc98\uc74c \ubc14\ub00c\uae30 \uc804 \uc0c1\ud0dc(\uac19\uc740 \ud134 \ub450 \ubc88\uc9f8 \ubc14\ub01c\uc740 \ub36e\uc9c0 \uc54a\uc74c)."""
    if (t.get("last") or {}).get("turn") == n:
        return
    t["last"] = {"turn": n, "conditions": copy.deepcopy(t["conditions"]), "items": list(t.get("items") or []),
                 "list_held": copy.deepcopy(t.get("list_held"))}


async def _deepen(
    dec: Decision, cur: dict, turn: dict, order: list[str], count: Count, *, created: bool = False, n: int = 0
):
    """깊게 — 채운 칸으로 다시 셈 · 고르기(시술 · 제품) 교체로 0 이면 그대로 따름(11-23) · 그 밖 0 이면 이번 턴 칸을 하나씩
    빼 본다(LS2 · 막은 값은 칸에 남기지 않음)."""
    if not getattr(count, "__wrapped_count__", False):
        count = suspend_aware(count)
    base = None if created else copy.deepcopy(cur["conditions"])
    merged = merge(base, turn)
    # SPEC-012 HP1 — 이번 턴 고르기 이름 가운데 효과 교집합에서 0 이 되는 이름이 있으면 효과는 칸에 두고 셈에서만 쉰다
    picks = [(s, v) for s in PICK for v in turn.get(s) or [] if v.get("from") != "product" and v.get("table") != "name"]
    if picks and merged.get("effect") and "effect" not in (merged.get("suspended") or []):
        dropped = []
        for s, v in picks:
            trial = copy.deepcopy(merged)
            for s2 in PICK:
                trial[s2] = [v] if s2 == s else []
            if not await count(trial):
                no_eff = copy.deepcopy(trial)
                no_eff["effect"] = []
                if await count(no_eff):
                    dropped.append(v.get("name") or v.get("code"))
        if dropped:
            merged["suspended"] = ["effect"]
            dec.rec["suspended"] = {"slots": ["effect"], "names": dropped}
    a_codes = await count(merged) if not is_empty(merged) else []
    dec.rec["A"] = len(a_codes)
    dec.changed = True
    if created:
        cur["last"] = {"turn": n, "created": True}
    else:
        snap(cur, n)
    if a_codes:
        cur["conditions"], cur["items"] = merged, list(a_codes)
        cur.pop("list_held", None)
        return dec
    if not created and any(turn.get(s) for s in PICK):
        cur["conditions"], cur["items"] = merged, []
        dec.rec["pick_zero"] = True
        return dec
    blocked, rest, kept = None, None, None
    for slot in reversed(order):
        trial = copy.deepcopy(merged)
        if slot == "country":
            trial["country"] = (base or {}).get("country")
        else:
            trial[slot] = copy.deepcopy((base or {}).get(slot) or [])
        got = await count(trial) if not is_empty(trial) else []
        if got:
            vals = (merged.get(slot) or []) if slot != "country" else [merged.get("country")]
            blocked = {
                "slot": slot,
                "values": [v.get("name") or v.get("code") for v in vals] if slot != "country" else vals,
                "without_n": len(got),
            }
            rest, kept = got, trial
            break
    if blocked is None:
        blocked = {"slot": "all", "slots": list(dict.fromkeys(order)), "without_n": None}
    dec.blocked = blocked
    cur["conditions"] = kept if kept is not None else (copy.deepcopy(base) if base else empty())
    if created and is_empty(cur["conditions"]):  # 막혀서 칸에 남은 조건이 0 이면 아직 주제 없음
        dec.topics.remove(cur)
        dec.active, dec.ls = None, "LS3"
        dec.rec.update(change=False, by=None)
        return dec
    if cur["items"]:
        dec.ls = "LS1"
        if blocked.get("slot") != "all":  # SPEC-012 HL1 — 조건은 kept · 목록은 앞 목록 → 표지
            cur["list_held"] = {"slot": blocked["slot"], "values": list(blocked.get("values") or [])}
            dec.rec["list_held"] = copy.deepcopy(cur["list_held"])
    else:
        dec.ls = "LS3"
        cur["items"] = list(rest or [])
    return dec


def visible_prev(topics: list[dict], cur: dict | None, max_n: int | None) -> list[dict]:
    """\ud55c\ub3c4(\uc9c0\uae08 + \uc55e max_n - 1) \uc548\uc758 \uc55e \uc8fc\uc81c(\ub9cc\ub4e0 \ucc28\ub840 \uc5ed\uc21c). \ud654\uba74 \u00b7 LLM \ube14\ub85d \u00b7 \ub418\ub3cc\ub9bc \ub300\uc0c1\uc774 \uac19\uc740 \ubaa9\ub85d\uc744 \uc4f4\ub2e4."""
    prev = sorted((t for t in topics if t is not cur), key=lambda t: t.get("created_turn") or 0, reverse=True)
    return prev if not max_n else prev[: max(0, max_n - 1)]


def switch(topics: list[dict], tid: str) -> dict | None:
    """\uba38\ub9ac \ub204\ub984(\ud134 \uc544\ub2d8)."""
    return _find(topics, tid)


# ---------- 테스트 고침 E(11-23) — 부위마다 다른 시술 · 제품이 짝지어 나오면 주제를 찢는다 ----------

PICK_TABLES = ("procedure", "modality", "procedure_case", "product")


def pair_groups(targets: list[dict]) -> list[dict]:
    """한 문장 안 차례대로 부위 → 시술 · 제품 짝 — 부위가 나오면 새 묶음, 시술 · 제품은 바로 앞 부위 묶음에 붙는다(부위보다
    먼저 나온 시술 · 제품은 부위 없는 묶음). 고민은 여기서 붙이지 않는다(부위 · 고민 연결은 허브 `concern_site` — turn.py)."""
    groups: list[dict] = []
    cur: dict | None = None
    for t in targets or []:
        tb = t.get("table")
        if tb == "site":
            cur = {"site": t, "picks": []}
            groups.append(cur)
        elif tb in PICK_TABLES:
            if cur is None:
                cur = {"site": None, "picks": []}
                groups.append(cur)
            cur["picks"].append(t)
    return groups


def should_split(groups: list[dict]) -> bool:
    """찢기 — 시술 · 제품을 가진 부위 묶음이 둘 이상이고 그 시술 · 제품 묶음이 서로 다를 때만(부위보다 먼저 나온 시술 · 제품이
    있으면 모든 부위에 걸리는 말이라 찢지 않음 · 부위 둘에 시술 하나 · 부위 하나에 시술 둘은 찢지 않음)."""
    if any(g["site"] is None and g["picks"] for g in groups):
        return False
    gs = [g for g in groups if g["site"] is not None and g["picks"]]
    if len(gs) < 2:
        return False
    keys = [frozenset((p.get("table"), p.get("code")) for p in g["picks"]) for g in gs]
    return len(set(keys)) > 1


async def split_decide(
    topics: list[dict], active: str | None, subs: list[dict], *, n: int, count: Count, llm: dict | None = None
) -> Decision:
    """찢기 판단 — `subs` = 짝마다 {turn(이번 턴 칸: 그 부위 · 그 부위 고민 · 그 시술 · 제품), site(부위 코드),
    keep_concern(지금 주제 고민 가운데 그 부위 고민)}.
    - 지금 주제가 부위 둘 이상이고 짝 부위를 가지면: 첫 짝은 지금 주제를 그 부위 · 그 부위 고민 · 그 시술로 좁혀 쓰고,
      다음 짝부터는 새 주제(지금 주제 칸에서 그 부위 · 그 부위 고민만 가져옴 + 그 시술).
    - 아니면: 그 짝 칸을 그대로 가진 주제(같은 값 — O-14)가 있으면 그 주제에 깊게, 없으면 새 주제.
    지금 주제 = 마지막 짝의 주제. 장부 `topic.split[{topic, site, picks, n}]` · 되돌리기 재료 `last` 는 다른 판단과 같은 꼴."""
    topics = copy.deepcopy(topics)
    for t in topics:
        t.setdefault("conditions", empty()).setdefault("exclude", [])
    cur = _find(topics, active) or (topics[-1] if topics else None)
    rec: dict = {
        "change": True,
        "by": "split",
        "move": "split",
        "conflict": [],
        "candidates": [],
        "llm_move": (llm or {}).get("move"),
        "llm_to": (llm or {}).get("to"),
        "to_from": None,
        "llm_agree": None,
        "info": False,
        "same_values": False,
        "undo": None,
        "widen": None,
        "pick_zero": False,
        "A": None,
        "split": [],
    }
    dec = Decision(topics, cur["id"] if cur else None, rec, changed=True)
    count = suspend_aware(count)
    rec["split_blocked"] = []  # SPEC-012 HS2 — 0 짝(그 부위는 명동점 진료항목 0 · 주제 안 만듦)
    cur_orig = copy.deepcopy(cur["conditions"]) if cur is not None else None
    cur_sites = {v.get("code") for v in (cur_orig or {}).get("site") or []}
    reused = False
    last_id = dec.active
    for sub in subs:
        turn = sub["turn"]
        t = None
        if cur is not None and len(cur_sites) > 1 and sub["site"] in cur_sites:
            cond = copy.deepcopy(cur_orig)
            cond["site"] = [v for v in cond.get("site") or [] if v.get("code") == sub["site"]]
            have = {_key(v) for v in sub.get("keep_concern") or []}
            cond["concern"] = list(sub.get("keep_concern") or []) + [
                v for v in turn.get("concern") or [] if _key(v) not in have
            ]
            cond = merge(cond, turn)
            got = await count(cond)
            if not got:
                rec["split_blocked"].append({"site": sub["site"], "values": cond_names_of(turn)})
                continue
            if not reused:
                snap(cur, n)
                cur["conditions"], cur["items"] = cond, list(got)
                cur["sites"] = [sub["site"]]
                t, reused = cur, True
            else:
                t = {
                    "id": _new_id(topics),
                    "conditions": cond,
                    "items": list(got),
                    "created_turn": n,
                    "last": {"turn": n, "created": True},
                    "sites": [sub["site"]],
                }
                topics.append(t)
        else:
            hold = [x for x in topics if holds(turn, x["conditions"])]
            if hold:
                t = hold[-1]
                m2 = merge(t["conditions"], turn)
                got = await count(m2)
                if not got:
                    rec["split_blocked"].append({"site": sub["site"], "values": cond_names_of(turn)})
                    continue
                snap(t, n)
                t["conditions"], t["items"] = m2, list(got)
            else:
                got = await count(turn)
                if not got:
                    rec["split_blocked"].append({"site": sub["site"], "values": cond_names_of(turn)})
                    continue
                t = {
                    "id": _new_id(topics),
                    "conditions": copy.deepcopy(turn),
                    "items": list(got),
                    "created_turn": n,
                    "last": {"turn": n, "created": True},
                    "sites": [sub["site"]],
                }
                topics.append(t)
        rec["split"].append(
            {
                "topic": str(t["id"]),
                "site": sub["site"],
                "picks": [
                    v.get("name") or v.get("code") for v in turn.get("procedure") or [] if v.get("from") != "product"
                ]
                + [v.get("name") or v.get("code") for v in turn.get("product") or []],
                "n": len(t["items"]),
            }
        )
        last_id = t["id"]
    dec.active = last_id
    if rec["split_blocked"]:
        dec.blocked = {"slot": "split", "sites": [b["site"] for b in rec["split_blocked"]],
                       "values": [v for b in rec["split_blocked"] for v in b["values"]], "without_n": None}
    return dec


def cond_names_of(c: dict) -> list[str]:
    return [v.get("name") or v.get("code") for s in SLOTS for v in c.get(s) or [] if v.get("from") != "product"]
