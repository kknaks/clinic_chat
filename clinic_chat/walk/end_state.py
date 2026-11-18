"""§5 종료 상태(B · 앱 판정) — 여섯 상태 · 「원천 미확정」 세 조건.

- 근거 충족 = 다 읽음 + 깊이 끝 + required 전부 **RQ1 로** 충족 + K1 ~ K5 / 후보 소진 = 어느 required 가 RQ1 넷째 줄.
- (가) 입구는 있는데 **그 대상의 그 범위(들) 조회 전체**에서 행 0 · `ledger_check[]` 합이 비지 않음(0.5.1 — 검수 v05 W-3 ①).
  읽은 조회가 전부 0 이고 그 `ledger_check[]` 합이 비지 않을 때만 그 범위의 나머지 조회를 **판정용**(`purpose` = `end_state`)으로
  불러 전부 0 인지 본다 — 그 행은 verified 에 넣지 않는다(K5). 나머지에 행이 있으면 (가) 가 아니다(RQ1 넷째 줄 = 후보 소진).
  **상위 고민 펼침 행(`hier.match = 상위`)은 셈에서 뺀다**(Q10 — 물은 코드 자신의 행). 읽은 범위 전부를 합쳐 본다.
  B 와 답 키 스크립트가 **같은 함수**(`unsettled_rows` · `needs_judgement`)를 쓴다.
- (나) 행은 있으나 전부 `reach` 가 못 닿음 · `ledger_check[]` 에 `on = rows` 항목
- (다) 1회차는 판정하지 않는다(종류 단위 required — O-5 ② 승인 · Q12 는 기대와 어긋날 것을 알고 간다)
`scope_gap` 은 상태가 아니라 표지다.
"""

from __future__ import annotations

from clinic_chat.walk.required import UPPER, rows_for_count
from clinic_chat.walk.verified import table_of

OK = "근거 충족"
EXHAUSTED = "후보 소진"
UNSETTLED = "원천 미확정"
ABORTED = "실행 중단"
OUT = "범위 밖"
NO_ENTRY = "입구 없음"
STATES = (OK, EXHAUSTED, UNSETTLED, ABORTED, OUT, NO_ENTRY)
# `reach` 의 「못 닿음」 값(SPEC-002 §8-6 표 — unreached[] 자리 · g_offer 의 「못 닿음」)
UNREACHED = ("패키지 — 항목 코드 없음", "제품 — 케이스 없음", "못 닿음")

GA_SKIP_TABLES = ("g_scope", "g_entry")  # 속성 행이 아닌 표(답 키 `rows[]` 에도 없다 — §9-5)
HIER_UPPER = "상위"


def counted_rows(raw: list[dict]) -> list[dict]:
    """(가) · (나) 를 셀 행 — 속성 행만 · 상위 펼침 행 제외(Q10 — package 값 쪽). 단 **윗고민으로 물려받은 응답의 행**
    (0.5.2 HG1 — 머리 `inherited_from` · RQ1 과 같은 표지 `required.UPPER`)은 허브가 물은 고민에 준 행이라 센다."""
    return [r for r in raw if table_of(r) not in GA_SKIP_TABLES
            and (r.get(UPPER) or (r.get("hier") or {}).get("match") != HIER_UPPER)]


def needs_judgement(raw: list[dict], lc: list[dict]) -> bool:
    """판정용 조회를 부를 조건 — 읽은 조회가 전부 0(셀 행 기준) · `ledger_check[]` 합이 비지 않음."""
    return not counted_rows(raw) and bool(lc)


def unsettled_rows(raw: list[dict], lc: list[dict], judgement: list[dict] | None = None) -> str | None:
    """(가) · (나) — B(`unsettled`)와 답 키 스크립트(`answer_key.expected_state`)가 같이 쓰는 판정.

    `raw` = 읽은 조회가 낸 행 전부(bucket 무관 · `holders` 제외) · `lc` = 그 조회들의 `ledger_check[]` 합 ·
    `judgement` = 판정용 조회(그 범위의 나머지 조회)가 낸 행 — 주지 않으면 읽은 것만으로 본다(0.4.1 과 같은 꼴).
    """
    rows = counted_rows(raw)
    if not rows and lc and not counted_rows(judgement or []):
        return "(가)"
    reach = [r.get("reach") for r in rows if "reach" in r]
    if rows and reach and len(reach) == len(rows) and all(v in UNREACHED for v in reach) \
            and any(x.get("on") == "rows" for x in lc):
        return "(나)"
    return None


def reads_rows(reads) -> list[dict]:
    """조회 응답 → 행(답 키 `read_rows` 와 같은 bucket 꼴 — `holders` 제외 · gid 로 한 번)."""
    out, seen = [], set()
    for r in reads:
        resp = dict(r.ra.as_response())
        resp.pop("holders", None)
        for row in rows_for_count(resp):
            g = row.get("gid")
            if g not in seen:
                seen.add(g)
                out.append(row)
    return out


def reads_lc(reads) -> list[dict]:
    return [x for r in reads for x in (r.ra.ledger_check or [])]


def decide(*, aborted: str | None, entry_end: str | None, unsettled: str | None, required_ok: bool,
           gate_ok: bool) -> tuple[str, str | None]:
    """(종료 상태, 사유 코드)."""
    if aborted:
        return ABORTED, aborted
    if entry_end:
        return entry_end, None
    if not gate_ok:
        return ABORTED, "gate"
    if unsettled:
        return UNSETTLED, unsettled
    if not required_ok:
        return EXHAUSTED, "required"
    return OK, None
