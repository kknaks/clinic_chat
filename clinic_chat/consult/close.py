"""끝맺음 하나 · 없음(SPEC-006 §4 CS1 ~ CS6 · §5 EM1 ~ EM6 · WORK-014 §10 · 코디 W-5 · W-8).

- `empties()` — 걷기가 그 (대상 × 정보 종류)의 조회를 **끝까지 읽었고 0 행**인 것만(EM1). 오류 · 쪽 넘김 미완 · 「실행 중단」
  은 `empty` 아님(EM2). 시너지(QY4 보완) 0 = 조합(EM5 — 그 턴 걷기의 종류에 조합이 든 턴만) · 리스트 0 인 고민 = 시술(EM6).
  장부 꼴 `{about{table, code, name}, what, tool, total: 0, complete: true}` · 「앱 판정」 꼴 `{about, what, safety}`(이름만).
- `alt()` — `focus` 고민 리스트의 계열 가운데 이번 대상의 계열이 아닌 것(허브 순서 · 앞에서 `CHAT_ALT_MAX` 개 · EM3).
- `pick()` — CS2: ① `ask` ② `empty` 가 있고 `alt` 가 있으면 `alt` ③ `offered`(안전 종류 빼고 — CS4) ④ `None`. 결정론.
"""

from __future__ import annotations

from clinic_chat.consult.blanks import SAFETY_KINDS
from clinic_chat.walk import end_state as es

READ_PURPOSES = ("read", "depth")
EM6_WHAT = "procedures"  # EM6 — 리스트 0(정보 종류 밖 · 이름은 `kind_labels.procedures`)
EM5_WHAT = "combo"


def _key(table: str, code: str, what: str) -> tuple[str, str, str]:
    return table, code, what


def empties(w, names: dict[str, str]) -> list[dict]:
    """EM1 · EM2 — 걷기 대상 × 읽은 종류 가운데 끝까지 읽어 0 행."""
    f = w.front
    if f is None or w.error or w.end_state in (es.ABORTED, es.OUT, es.NO_ENTRY):
        return []
    nodes = {e.node for e in f.entries}
    kinds = set(f.kinds)
    agg: dict[tuple[str, str, str], dict] = {}
    for r in w.reads:
        c = r.call
        if c.purpose not in READ_PURPOSES or c.node() not in nodes:
            continue
        table, _, code = c.node().partition(":")
        for k in c.kinds:
            if k not in kinds:
                continue
            a = agg.setdefault(_key(table, code, k), {"total": 0, "complete": True, "tool": c.tool})
            a["total"] += (r.ra.total or 0) + _outside_rows(r)
            a["complete"] = a["complete"] and bool(r.ra.complete) and not getattr(r.ra, "error", None)
    out = []
    for (table, code, k), a in agg.items():
        if a["complete"] and a["total"] == 0:
            out.append({"about": {"table": table, "code": code, "name": names.get(f"{table}:{code}", "")}, "what": k,
                        "tool": a["tool"], "total": 0, "complete": True})
    return out


def _outside_rows(r) -> int:
    """`concern_candidates` 의 `total` 은 묶음(`groups`) 수다 — 「계열 층만」 · 「그 밖」 · 내려온 행이 있으면 0 행이 아니다."""
    if r.call.tool != "concern_candidates" or not hasattr(r.ra, "as_response"):
        return 0
    b = r.ra.as_response() or {}
    return len(b.get("modality_only") or []) + len(b.get("unreached") or []) + len(b.get("descended") or [])


def synergy_empty(obj: dict, queries: list[dict], kinds_read: list[str]) -> list[dict]:
    """EM5 — 조합을 물은 턴에만: 이번 턴 QY4(보완) 조회가 끝까지 읽어 0 행인 대상."""
    if EM5_WHAT not in kinds_read:
        return []
    out = []
    for q in queries:
        if q.get("rule") != "QY4" or q.get("error") or q.get("total") != 0:
            continue
        node = str((q.get("request") or {}).get("a") or "")
        table, _, code = node.partition(":")
        it = next((x for x in obj.get("subjects") or [] if x.get("table") == table and x.get("code") == code), {})
        out.append({"about": {"table": table, "code": code, "name": it.get("name") or ""}, "what": EM5_WHAT,
                    "tool": "pair", "total": 0, "complete": True})
    return out


def list_empty(list_state: dict | None) -> list[dict]:
    """EM6 — 리스트가 0 인 고민(내려온 묶음까지 0 · 물려받은 리스트도 없음)."""
    out = []
    for c in (list_state or {}).get("concerns") or []:
        n_procs = sum(len(m.get("procedures") or []) for m in c.get("modalities") or [])
        n_desc = sum(len(m.get("procedures") or []) for m in c.get("descended") or []) \
            + ((c.get("hidden") or {}).get("descended_n") or 0)
        if n_procs == 0 and n_desc == 0 and not c.get("inherited_from"):
            out.append({"about": {"table": "concern", "code": c["concern_code"], "name": c.get("concern_name") or ""},
                        "what": EM6_WHAT, "tool": "concern_candidates", "total": 0, "complete": True})
    return out


def merge(*groups: list[dict]) -> list[dict]:
    seen, out = set(), []
    for g in groups:
        for x in g:
            k = (x["about"]["table"], x["about"]["code"], x["what"])
            if k not in seen:
                seen.add(k)
                out.append(x)
    return out


def view(empty: list[dict], kind_labels: dict[str, str]) -> list[dict]:
    """「앱 판정」 `empty[]` — 이름만 · `safety`. 답 규칙(고침 1)에서 이 칸은 「말하지 않는 칸」 표지다 — 「없어요」 문장 재료가 아니다
    (`safety` 참이면 없다 · 안전하다로 말하지 않고 「상담 때 원장님과 확인」 으로 돌려 말한다)."""
    return [{"about": x["about"].get("name") or "", "what": kind_labels.get(x["what"], ""),
             "safety": x["what"] in SAFETY_KINDS} for x in empty]


def alt(list_state: dict | None, focus: str | None, target_mods: set[str], limit: int) -> list[dict]:
    """EM3 — `focus` 고민 리스트의 계열 가운데 이번 대상의 계열이 아닌 것(허브 순서 · 이름 있는 것만)."""
    if not focus:
        return []
    out = []
    for c in (list_state or {}).get("concerns") or []:
        if c.get("concern_code") != focus:
            continue
        for m in [*(c.get("modalities") or []), *(c.get("descended") or [])]:
            code = m.get("modality_code") or ""
            if not code or code in target_mods or not m.get("modality_name") or any(a["code"] == code for a in out):
                continue
            out.append({"table": "modality", "code": code, "name": m["modality_name"]})
    return out[:max(0, limit)]


def pick(ask: dict | None, empty: list[dict], alts: list[dict], offered: list[str]) -> dict:
    """CS1 · CS2 · CS4 — 장부 `close{by, alt[], offered[], said}`."""
    offered = [k for k in offered if k not in SAFETY_KINDS]
    if ask is not None:
        by = "ask"
    elif empty and alts:
        by = "alt"
    elif offered:
        by = "offered"
    else:
        by = None
    return {"by": by, "alt": alts if by == "alt" else [], "offered": offered if by == "offered" else [], "said": None}


def close_view(close: dict, ask_view: dict | None, kind_labels: dict[str, str]) -> dict:
    """「앱 판정」 끝맺음 블록 — 이름만."""
    out: dict = {"by": close["by"]}
    if close["by"] == "ask":
        out["ask"] = ask_view
    elif close["by"] == "alt":
        out["alt"] = [a["name"] for a in close["alt"]]
    elif close["by"] == "offered":
        out["offered"] = [kind_labels.get(k, "") for k in close["offered"]]
    return out
