"""조건 칸 → 명동점 진료항목(SPEC-010 §5-1 ④ ⑤ · §5-2 · SF1 ~ SF3 · NM1 ~ NM7 · CZ1 · CD6).

- 칸 다섯 + 국가: procedure(procedure · modality · procedure_case · name) · site · concern · effect · product · country.
- 칸 안 값 = 합집합 · 칸끼리 = 교집합(값이 있는 칸만 · D-13).
- 고민 길 = `concern_candidates`(upper auto · to_procedure none · 쪽 넘김 끝까지)의 행 가운데 reach 「케이스 직접」 만 → by_case
  + 테스트 고침 A-4(사용자 11-22): 그 케이스와 **같은 시술**이고 매핑 부위가 **그 케이스 부위를 품는**(같음 · 조상) 진료항목
  (팔자주름 → HA 필러 × 팔자주름 → 얼굴필러). 케이스 부위 = 행 `site_code`, 비면 `case_site_name` 과 이름이 같은 `$J` 부위(하나일 때만).
- 효과 길 = `facets outcome node=effect:` 의 procedure_case · procedure 행만(N2) — modality · package · product 행은 버리고 센다.
- CZ1 — 부위 · 제품 · 효과 · 고민 · 국가 칸에서 온 집합은 케이스 0 진료항목을 뺀다(이름 일치로만 들어온다).
- 한 턴 안 같은 (도구, 인자) = 한 번(`cache`) · 허브 오류는 그 근거만 빼고(`errors[]`) 나머지로 짓는다(§5-2).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from clinic_chat.consult.kos_map import KosMap

SLOTS = ("procedure", "site", "concern", "effect", "product")  # 칸 순서(IL11 머리 · LS 셈)
PICK_SLOTS = ("procedure", "product")  # SPEC-012 고침 2 — 고르기 칸
SLOT_OF_TABLE = {
    "procedure": "procedure",
    "modality": "procedure",
    "procedure_case": "procedure",
    "name": "procedure",
    "site": "site",
    "concern": "concern",
    "effect": "effect",
    "product": "product",
}
CASE_DIRECT = "\ucf00\uc774\uc2a4 \uc9c1\uc811"  # 허브 reach 값(앱 문장 아님)
SITE_DIRECT = "\uc9c1\uc811"  # 허브 `sites[].match` 값 「직접」(앱 문장 아님)
CC_REQ = {"upper": "auto", "to_procedure": "none"}
SUB_PRODUCT = "\ud558\uc704 \uc81c\ud488"  # 허브 scope role 값
KR = "KR"
SCOPE_FACE = (
    "\uc5bc\uad74 \uc804\uccb4",
    "\uc5b4\ub514\ub4e0(\ubd80\uc704 \ubb34\uad00)",
    "\uc5bc\uad74 \xb7 \ubab8 \uac00\ub2a5",
)  # SF2 원문 값
SCOPE_BODY = (
    "\ubab8",
    "\uc5b4\ub514\ub4e0(\ubd80\uc704 \ubb34\uad00)",
    "\uc5bc\uad74 \xb7 \ubab8 \uac00\ub2a5",
)  # SF3 원문 값
GENERIC_NAMES = (
    "\uc2a4\ud0a8\ucf00\uc5b4",
    "\ubbf8\ubc31\uad00\ub9ac",
    "\uc9c4\uc815\uad00\ub9ac",
    "\uc218\uba74\ub9c8\ucde8",
)  # NM6 일반 낱말 이름(표지만)
ASCII_SHORT = 3


def compact(s: str) -> str:
    return "".join((s or "").split())


# ---------- 이름 일치(NM1 ~ NM7) ----------


@dataclass
class NameHit:
    name: str
    kind: str  # item | kos_procedure
    items: list[str]
    start: int
    end: int
    generic: bool = False

    def view(self) -> dict:
        return {"name": self.name, "kind": self.kind, "items": self.items, "generic": self.generic}


def _ascii_ok(text: str, i: int, j: int) -> bool:
    """NM6 — ASCII 짧은 이름은 앞뒤가 ASCII 글자 · 숫자가 아닐 때만."""
    before = text[i - 1] if i > 0 else ""
    after = text[j] if j < len(text) else ""
    return not (before.isascii() and before.isalnum()) and not (after.isascii() and after.isalnum())


def name_hits(kos: KosMap, utterance: str) -> list[NameHit]:
    """NM2 공백 지운 글자끼리 · NM3 겹치면 긴 것 · NM4 같은 이름 전부 · NM5 진료항목 이름 먼저 · NM6 ASCII 낱말 경계."""
    text = compact(utterance)
    if not text:
        return []
    cands: dict[str, dict[str, list[str]]] = {}  # compact 이름 → kind → 진료항목
    names: dict[str, str] = {}
    for it in kos.items:
        k = compact(it.get("kos_item_name") or "")
        if k:
            cands.setdefault(k, {}).setdefault("item", []).append(it["kos_item_code"])
            names.setdefault(k, it.get("kos_item_name") or "")
    for g in kos.groups:
        k = compact(g["kos_procedure"])
        if k:
            cands.setdefault(k, {})["kos_procedure"] = list(g["items"])
            names.setdefault(k, g["kos_procedure"])
    found: list[tuple[int, int, str]] = []
    for k in cands:
        for m in re.finditer(re.escape(k), text):
            i, j = m.start(), m.end()
            if k.isascii() and len(k) <= ASCII_SHORT and not _ascii_ok(text, i, j):
                continue
            found.append((i, j, k))
    found.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
    taken: list[tuple[int, int]] = []
    out: list[NameHit] = []
    for i, j, k in found:
        if any(i < b and a < j and (b - a) > (j - i) for a, b in taken):
            continue  # NM3 — 더 긴 이름이 이미 덮은 자리
        taken.append((i, j))
        for kind in ("item", "kos_procedure"):  # NM5 — 진료항목 이름 먼저
            if kind in cands[k]:
                codes = kos.sort_codes(cands[k][kind])
                out.append(NameHit(names[k], kind, codes, i, j, names[k] in GENERIC_NAMES))
    out.sort(key=lambda h: (h.start, 0 if h.kind == "item" else 1))
    return out


# ---------- 칸 → 진료항목 ----------


@dataclass
class Result:
    codes: list[str] = field(default_factory=list)
    by: dict[str, list[str]] = field(default_factory=dict)  # 코드 → 칸
    per_slot: dict[str, int] = field(default_factory=dict)
    dropped_reach: dict[str, int] = field(default_factory=dict)
    upper: list[str] = field(default_factory=list)  # E3 — 물려받은 고민 행으로 온 고민
    errors: list[dict] = field(default_factory=list)
    country_dropped: int = 0  # CD6 — 국가 빈칸이라 뺀 진료항목 수
    excluded: int = 0  # 뺌 칸으로 뺀 진료항목 수(주제 튜플 11-22)
    case_scope: dict[str, dict] = field(
        default_factory=dict
    )  # A-4 — 케이스 → {site, added[]}(같은 시술 · 부위 품음으로 더한 것)


class Detector:
    """한 턴 몫 — `cache` 는 (도구, 인자) → 응답(같은 턴 같은 호출 한 번). `countries` 는 제품 국가(프로세스 캐시 가능)."""

    def __init__(self, kos: KosMap, hub, cache: dict | None = None, countries: dict | None = None) -> None:
        self.kos, self.hub = kos, hub
        self.cache = cache if cache is not None else {}
        self.countries = countries if countries is not None else {}
        self.hub_calls = 0
        self.errors: list[dict] = []  # 이 Detector 로 센 모든 셈의 허브 오류(WP-3 검수 P1-1 — 턴 판단은 turn.py)
        self.casezero = {i["kos_item_code"] for i in kos.items if not i["hub"].get("case_codes")}

    async def _read(self, tool: str, req: dict) -> list:
        key = (tool, json.dumps(req, sort_keys=True, ensure_ascii=False))
        if key not in self.cache:
            self.hub_calls += 1
            ra = await self.hub.read_all(tool, req)
            if not ra.complete:
                raise RuntimeError(ra.error or "incomplete")
            self.cache[key] = (list(ra.items), dict(ra.head or {}))
        return self.cache[key][0]

    def _by(self, index: dict, code: str) -> set[str]:
        return set(index.get(code, ()))

    def concern_sites(self, code: str) -> list[str]:
        """A-1 — 고민의 부위(`concern_candidates` 응답 머리 `sites[]` 가운데 match 「직접」 · 이 턴에 읽은 것만 · 허브 순서)."""
        key = ("concern_candidates", json.dumps({"concern_code": code, **CC_REQ}, sort_keys=True, ensure_ascii=False))
        head = (self.cache.get(key) or ([], {}))[1]
        out = [
            str(x.get("site_code"))
            for x in head.get("sites") or []
            if isinstance(x, dict) and x.get("site_code") and x.get("match") in (None, SITE_DIRECT)
        ]
        return list(dict.fromkeys(out))

    def case_site(self, row: dict) -> str | None:
        """A-4 — 케이스 부위 코드(행 `site_code` → 없으면 `case_site_name` 과 이름이 같은 `$J` 부위 · 같은 이름이 둘이면 None)."""
        k = self.kos
        sc = row.get("site_code") or ""
        if sc in k.sites:
            return sc
        if not hasattr(self, "_site_by_name"):
            names: dict[str, list[str]] = {}
            for c, v in k.sites.items():
                names.setdefault(v.get("name") or "", []).append(c)
            self._site_by_name = {n: cs[0] for n, cs in names.items() if n and len(cs) == 1}
        return self._site_by_name.get(row.get("case_site_name") or "")

    def same_procedure_scope(self, proc: str, site: str) -> set[str]:
        """A-4 — 그 시술(`hub.procedure_codes`)이고 매핑 부위가 `site` 를 품는(같음 · 조상) 진료항목."""
        k = self.kos
        cover = {site, *k.ancestors(site)}
        return {c for c in k.by_proc.get(proc, ()) if cover & set(k.item[c]["hub"].get("site_codes") or [])}

    async def concern(self, code: str, res: Result) -> set[str]:
        groups = await self._read("concern_candidates", {"concern_code": code, **CC_REQ})
        out: set[str] = set()
        for g in groups:
            for r in g.get("rows") or []:
                if r.get("reach") == CASE_DIRECT and r.get("candidate_table") == "procedure_case":
                    case = r.get("candidate_code") or ""
                    direct = self._by(self.kos.by_case, case)
                    out |= direct
                    site = self.case_site(r)
                    if site and r.get("procedure_code") and case not in res.case_scope:
                        more = self.same_procedure_scope(r["procedure_code"], site) - direct
                        res.case_scope[case] = {"site": site, "added": self.kos.sort_codes(more)}
                        out |= more
                    if r.get("concern_code") and r.get("concern_code") != code and r["concern_code"] not in res.upper:
                        res.upper.append(r["concern_code"])
                else:
                    res.dropped_reach[str(r.get("reach") or "")] = (
                        res.dropped_reach.get(str(r.get("reach") or ""), 0) + 1
                    )
        return out

    async def modality(self, code: str) -> set[str]:
        holders = await self._read("scope", {"node": f"modality:{code}"})
        out: set[str] = set()
        for h in holders:
            if h.get("holder_table") == "procedure":
                out |= self._by(self.kos.by_proc, h.get("holder_code") or "")
        return out

    async def product(self, code: str) -> set[str]:
        out = self._by(self.kos.by_product, code)
        holders = await self._read("scope", {"node": f"product:{code}"})
        for h in holders:
            if h.get("role") == SUB_PRODUCT and h.get("holder_table") == "product":
                out |= self._by(self.kos.by_product, h.get("holder_code") or "")
        return out

    async def effect(self, code: str, res: Result) -> set[str]:
        rows = await self._read("facets", {"facet": "outcome", "node": f"effect:{code}"})
        out: set[str] = set()
        for r in rows:
            t, c = r.get("target_table"), r.get("target_code") or ""
            if t == "procedure_case":
                out |= self._by(self.kos.by_case, c)
            elif t == "procedure":
                out |= self._by(self.kos.by_proc, c)
            else:
                res.dropped_reach[str(t)] = res.dropped_reach.get(str(t), 0) + 1  # N2 — modality · package · product
        return out

    def site(self, code: str) -> set[str]:
        """SF2 · SF3 — 자손(자기 포함) + 조상 규칙. 얼굴 부위면 맨 위 조상 「얼굴」 만(`site_scope` 얼굴 규칙) · 몸 부위면 조상
        「몸」 · 「전신」(몸 규칙) — 얼굴 부위에 몸 조상은 넣지 않는다(SF3). 뿌리는 `sites` 의 부모 사슬에서 찾는다."""
        k = self.kos
        if code not in k.sites:
            return set()
        under = set(k.descendants(code))
        anc = k.ancestors(code)
        face = k.sites[code].get("region") == "face"
        if face:
            up = {anc[-1]} if anc else set()  # 얼굴 뿌리(SITE-007)
            scopes = SCOPE_FACE
        else:
            up = set(anc)  # 몸 · 전신(SITE-147 · SITE-001)
            scopes = SCOPE_BODY
        out: set[str] = set()
        for it in k.items:
            sc = set(it["hub"].get("site_codes") or [])
            if sc & under or (sc & up and it.get("site_scope") in scopes):
                out.add(it["kos_item_code"])
        return out

    async def country_of(self, prd: str) -> str:
        if prd not in self.countries:
            rows = await self._read("facets", {"facet": "profile", "node": f"product:{prd}"})
            own = [r for r in rows if r.get("subject_table") == "product" and r.get("subject_code") == prd]
            self.countries[prd] = str((own[0] if own else {}).get("country") or "")
        return self.countries[prd]

    async def country(self, want: str, among: set[str] | None = None) -> tuple[set[str], set[str]]:
        """CD6 — 제품 하나라도 국가가 맞으면 · 국가 빈칸 제품만 가진 진료항목은 맞지 않음(둘째 값 = 그 진료항목).
        `among` = 다른 칸 교집합(그 진료항목의 제품만 국가를 묻는다 — 검수 P2-4 · 첫 턴 허브 호출을 줄임)."""
        out: set[str] = set()
        blank: set[str] = set()
        for it in self.kos.items:
            if among is not None and it["kos_item_code"] not in among:
                continue
            prds = [p["code"] for p in it["hub"].get("products") or [] if p.get("code")]
            if not prds:
                continue
            cs = [await self.country_of(p) for p in prds]
            ok = any((c == KR) if want == KR else (c and c != KR) for c in cs)
            if ok:
                out.add(it["kos_item_code"])
            elif all(not c for c in cs):
                blank.add(it["kos_item_code"])
        return out, blank

    async def value_set(self, slot: str, v: dict, res: Result) -> set[str] | None:
        t, c = v.get("table"), v.get("code") or ""
        try:
            if t == "name":
                return set(v.get("items") or [])
            if t == "procedure":
                return self._by(self.kos.by_proc, c)
            if t == "procedure_case":
                return self._by(self.kos.by_case, c)
            if t == "modality":
                return await self.modality(c)
            if t == "concern":
                return await self.concern(c, res)
            if t == "effect":
                return await self.effect(c, res)
            if t == "product":
                return await self.product(c)
            if t == "site":
                return self.site(c)
        except Exception as e:  # noqa: BLE001 — §5-2 그 근거만 빼고
            err = {"slot": slot, "table": t, "code": c, "kind": type(e).__name__, "detail": str(e)[:120]}
            res.errors.append(err)
            self.errors.append(err)
            return None  # 오류 = 「0 결과」 아님(검수 P1-1)
        return set()

    async def items(self, conds: dict) -> Result:
        """칸 → 진료항목(교집합) · 순서 = 이름 일치 진료항목 → groups 순서."""
        res = Result()
        sets: list[set[str]] = []
        name_first: list[str] = []
        pick: dict[str, list[tuple[dict, set[str]]]] = {}  # SPEC-012 고침 2 — 고르기 칸(시술 · 제품) 값별 집합
        both = bool(conds.get("procedure")) and bool(conds.get("product"))
        for slot in SLOTS:
            vals = conds.get(slot) or []
            if not vals:
                continue
            u: set[str] = set()
            ok = 0
            for v in vals:
                got = await self.value_set(slot, v, res)
                if got is None:
                    continue  # 오류 난 값은 그 칸 합집합에서만 빠진다
                ok += 1
                if v.get("table") != "name":
                    got -= self.casezero  # CZ1 — 케이스 0 진료항목은 이름 일치로만(U-9)
                else:
                    name_first += [x for x in v.get("items") or [] if x not in name_first]
                u |= got
                if slot in PICK_SLOTS:
                    pick.setdefault(slot, []).append((v, set(got)))
            if not ok:
                continue  # 칸의 모든 값이 오류면 그 칸은 교집합에서 뺀다(§5-2 「그 근거만 빼고」)
            res.per_slot[slot] = len(u)
            for x in u:
                res.by.setdefault(x, []).append(slot)
            if both and slot in PICK_SLOTS:
                continue  # 둘 다 있으면 아래에서 한 집합(합집합)으로
            sets.append(u)
        if pick.get("procedure") is not None and pick.get("product") is not None:
            # SPEC-012 고침 2(#13 「이름 댄 것 다」) — 시술 칸과 제품 칸에 따로 잡힌 이름은 서로 지우지 않는다(합집합).
            # 제품의 시술(`from: product`)은 그 제품이 대신하고, 직접 말한 시술이 어떤 제품 집합을 품으면(보톡스 ⊇ 하이톡스)
            # 그 제품이 그 시술을 좁힌 것으로 본다(그 시술 값은 빼고 제품 집합만).
            # 고침 3(r3 P2-3) — 포함 관계는 양쪽으로 본다(좁은 쪽이 이김): 직접 말한 시술 · 이름 일치 집합이 어떤 제품 집합 안에
            # 들면(아이써마지 ⊂ 써마지) 그 제품의 좁은 이름을 댄 것으로 보고 그 제품 집합은 합집합에 넣지 않는다(집합 포함만 · 낱말 0).
            direct = [g for v, g in pick["procedure"] if v.get("from") != "product" and g]
            prods = [g for _, g in pick["product"]]
            narrowed = [p for p in prods if any(g < p for g in direct)]
            kept = [p for p in prods if not any(g < p for g in direct)]
            u = set().union(*kept) if kept else set()
            for v, g in pick["procedure"]:
                if v.get("from") == "product":
                    continue
                if any(p and p <= g for p in kept):
                    continue
                u |= g
            if narrowed:
                res.per_slot["pick_narrowed"] = len(narrowed)
            res.per_slot["pick"] = len(u)
            sets.append(u)
        if conds.get("country"):
            blank: set[str] = set()
            others = set.intersection(*sets) if sets else set(self.kos.item) - self.casezero
            try:
                u, blank = await self.country(conds["country"], among=others if sets else None)
                u -= self.casezero
                res.country_dropped = len(others & blank)  # R7 — 다른 칸을 만족하지만 국가 빈칸이라 뺀 수
                res.per_slot["country"] = len(u)
                sets.append(u)
            except Exception as e:  # noqa: BLE001 — 국가 칸도 그 근거만 빼고
                err = {"slot": "country", "kind": type(e).__name__, "detail": str(e)[:120]}
                res.errors.append(err)
                self.errors.append(err)
        if not sets:
            return res
        inter = set.intersection(*sets)
        out: set[str] = set()
        for v in conds.get("exclude") or []:  # 주제 튜플(11-22) — 뺌 칸: 그 값의 진료항목을 뺀다(좁힘)
            got = await self.value_set(SLOT_OF_TABLE.get(str(v.get("table")), "procedure"), v, res)
            out |= got or set()
        if out:
            res.excluded = len(inter & out)
            inter -= out
        first = [x for x in name_first if x in inter]
        res.codes = first + [x for x in self.kos.sort_codes(inter) if x not in first]
        res.by = {k: v for k, v in res.by.items() if k in inter}
        return res
