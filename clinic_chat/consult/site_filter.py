"""이번 턴 부위 거르개(SPEC-008 VF8 ⓒ · OU7 · QS3 · WORK-017 WP-4) → 장부 `consult.site_filter`.

- 바탕 = 검증 뒤 `sites[]`(VF8 — `targets` 의 부위 가운데 `sites` 에 없는 것은 거르개가 아니다).
- OU7 부위 잇기 — 넷이 다 맞을 때만: ⓐ 이번 턴 `targets` 가 전부 `concern` ⓑ 이번 턴 `sites` 가 `[]` ⓒ 바로 앞 **성공** 턴(실패
  턴은 건너뛴다)의 검증 뒤 `targets` 가 전부 `site` ⓓ 그 턴 `consult.ask.slot` = `concern` 이고 `about` 이 그 부위 → 그 턴의
  `site_filter.sites`(없으면 그 턴 `targets` 의 부위)를 `carried` · `from` = 그 턴 번호.
- QS3 — 같은 이름의 부위(객체 항목 `source.pair_concern`)는 짝 고민이 `focus` 일 때만 거르개(`pair_kept` · `pair_dropped`).
"""

from __future__ import annotations

from clinic_chat.consult import state as cst


def prev_ok(turns: list[dict]) -> dict | None:
    """바로 앞 성공 턴 — 오류 없고 이해 호출이 실패하지 않은 켠 줄(실패 턴은 건너뛴다)."""
    for t in reversed(turns):
        u = (t.get("consult") or {}).get("understand")
        if t.get("error") is None and u is not None and u.get("error") is None:
            return t
    return None


def _targets(row: dict) -> list[dict]:
    return (((row.get("consult") or {}).get("understand") or {}).get("accepted") or {}).get("targets") or []


def decide(acc, obj: dict, prev_row: dict | None, focus: str | None) -> dict:
    sites = list(acc.sites)
    carried: list[str] = []
    frm = None
    tg = acc.targets
    if tg and all(x["table"] == "concern" for x in tg) and not acc.sites and prev_row is not None:
        pt = _targets(prev_row)
        ask = (prev_row.get("consult") or {}).get("ask") or {}
        if pt and all(x["table"] == "site" for x in pt) and ask.get("slot") == "concern":
            psites = list(((prev_row.get("consult") or {}).get("site_filter") or {}).get("sites") or []) \
                or [x["code"] for x in pt]
            if ask.get("about") in {f"site:{c}" for c in psites}:
                carried = psites
                frm = prev_row.get("turn")
                sites = list(dict.fromkeys([*sites, *carried]))
    kept: list[str] = []
    dropped: list[str] = []
    out: list[str] = []
    for c in sites:
        it = cst.find(obj, "site", c)
        pc = ((it or {}).get("source") or {}).get("pair_concern")
        if pc is None:
            out.append(c)
        elif pc == focus:
            out.append(c)
            kept.append(c)
        else:
            dropped.append(c)
    return {"sites": out, "carried": carried, "from": frm, "pair_kept": kept, "pair_dropped": dropped}
