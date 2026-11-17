"""상담 0.2(상담 객체) 시험용 모의 허브 · Jev — 가짜 코드 · 가짜 이름 · 손으로 적은 꼴(규칙 계산 없음).

허브 꼴은 실물 18105 의 키(WORK-012 W-6 확인 — `scope.holders[]` · `facets` indication `rows[].concern_code` · `layer` ·
`pair.rows[]` 의 `from_*` · `to_*` · `a_side` · `a_layer` · `question_class` · `same_day` · `goal_effects`)를 따른다.

- 개념(`resolve` — 글에 이름이 통째로 들면 후보): 제품 ZZR-1 「가짜제품」 · 시술 ZZP-1 「가짜시술」 · 계열 ZZM-1 「가짜계열」 ·
  고민 ZZC-1 「가짜고민」(소분류) · ZZC-2 「가짜윗고민」(하위 고민 ZZC-3 · ZZC-4) · 같은 이름 고민 ZZC-9 / 부위 ZZS-9 「가짜팔자」 ·
  부위 ZZS-1 「가짜부위」 · 사건 ZZA-1 「가짜사건」.
- `scope`: 제품 ZZR-1 → 자기 + 물려받음(케이스 ZZK-1 · 시술 ZZP-1 · 계열 ZZM-1).
- `facets` indication(대상 쪽): 제품 ZZR-1 → 고민 ZZC-1(물려받음 · 시술 층) · ZZC-2(물려받음 · 계열 층) · 부위 ZZS-1 → ZZC-1.
- `concern_candidates`: ZZC-1 → 시술 ZZP-1(계열 ZZM-1) · ZZP-2(계열 ZZM-2) · ZZC-2(윗고민) → 하위 고민 행 ZZC-3 · ZZC-4.
- `pair`(a = 제품 ZZR-1 · [보완]) → 상대 ZZM-1(리스트 안) · ZZP-2(리스트 안) · ZZM-8(리스트 밖) · 회피 갈래 = ZZM-1 에 「불가」.
"""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from clinic_chat.columns.base import Deps
from clinic_chat.columns.col_b import TAIL_MARK
from clinic_chat.consult.server import create_app
from tests.conftest import make_settings, stream
from tests.fake_hub import FakeHub, row
from tests.fake_jev import FakeJev

CONCEPTS = [
    ("product", "ZZR-1", "가짜제품"), ("procedure", "ZZP-1", "가짜시술"), ("modality", "ZZM-1", "가짜계열"),
    ("concern", "ZZC-1", "가짜고민"), ("concern", "ZZC-2", "가짜윗고민"), ("concern", "ZZC-9", "가짜팔자"),
    ("site", "ZZS-9", "가짜팔자"), ("site", "ZZS-1", "가짜부위"), ("adverse", "ZZA-1", "가짜사건"),
]


def lay(role: str, table: str, code: str, name: str) -> dict:
    return {"role": role, "path": "", "holder": {"holder_table": table, "holder_code": code, "holder_name": name}}


def resolve_fn(p: dict) -> dict:
    t = p.get("text") or ""
    out = []
    for table, code, name in CONCEPTS:
        i = t.find(name)
        if i >= 0:
            out.append({"gold_table": "g_entry", "gid": f"GEN-{code}", "entity_table": table, "entity_code": code,
                        "entity_name": name, "text": name, "norm": name,
                        "matched": {"span": name, "start": i, "covered_by": None}})
    return {"candidates": out, "ambiguous": False, "total": len(out), "next_cursor": None}


SCOPE = {
    "product:ZZR-1": [row("g_scope", "GSC-90001", holder_table="product", holder_code="ZZR-1", holder_name="가짜제품",
                          role="자기"),
                      row("g_scope", "GSC-90002", holder_table="procedure_case", holder_code="ZZK-1",
                          holder_name="가짜케이스", role="물려받음"),
                      row("g_scope", "GSC-90003", holder_table="procedure", holder_code="ZZP-1", holder_name="가짜시술",
                          role="물려받음"),
                      row("g_scope", "GSC-90004", holder_table="modality", holder_code="ZZM-1", holder_name="가짜계열",
                          role="물려받음")],
    "procedure:ZZP-1": [row("g_scope", "GSC-90011", holder_table="procedure", holder_code="ZZP-1",
                            holder_name="가짜시술", role="자기")],
    "modality:ZZM-1": [row("g_scope", "GSC-90021", holder_table="modality", holder_code="ZZM-1", holder_name="가짜계열",
                           role="자기")],
}
INDICATION = {
    "product:ZZR-1": [
        row("g_indication", "GIN-90001", concern_code="ZZC-1", concern_name="가짜고민",
            layer=lay("물려받음", "procedure", "ZZP-1", "가짜시술")),
        row("g_indication", "GIN-90002", concern_code="ZZC-2", concern_name="가짜윗고민",
            layer=lay("물려받음", "modality", "ZZM-1", "가짜계열")),
        row("g_indication", "GIN-90003", concern_code="ZZC-1", concern_name="가짜고민",
            layer=lay("물려받음", "modality", "ZZM-1", "가짜계열"))],
    "site:ZZS-1": [row("g_indication", "GIN-90011", concern_code="ZZC-1", concern_name="가짜고민",
                       layer=lay("자기", "procedure", "ZZP-1", "가짜시술"))],
}


def cc_group(concern: str, cname: str, ptable: str, pcode: str, pname: str, gid: str, match: str = "직접",
             row_concern: tuple[str, str] | None = None) -> dict:
    rc = row_concern or (concern, cname)
    return {"candidate_table": ptable, "candidate_code": pcode, "candidate_name": pname, "procedure_code": pcode,
            "procedure_name": pname, "reach": ["시술 직접"], "concern_match": [match], "n_rows": 1,
            "rows": [row("g_indication", gid, candidate_table=ptable, candidate_code=pcode, procedure_code=pcode,
                         reach="시술 직접", concern_match=match, concern_code=rc[0], concern_name=rc[1],
                         asked_concern_code=concern, asked_concern_name=cname)]}


CC = {
    "ZZC-1": [cc_group("ZZC-1", "가짜고민", "procedure", "ZZP-1", "가짜시술", "GIN-91001"),
              cc_group("ZZC-1", "가짜고민", "procedure", "ZZP-2", "가짜시술둘", "GIN-91002")],
    "ZZC-2": [cc_group("ZZC-2", "가짜윗고민", "procedure", "ZZP-1", "가짜시술", "GIN-92001", "하위 고민",
                       ("ZZC-3", "가짜하위하나")),
              cc_group("ZZC-2", "가짜윗고민", "procedure", "ZZP-2", "가짜시술둘", "GIN-92002", "하위 고민",
                       ("ZZC-4", "가짜하위둘"))],
    "ZZC-9": [cc_group("ZZC-9", "가짜팔자", "procedure", "ZZP-1", "가짜시술", "GIN-99001")],
}
MODS = {"ZZP-1": ("ZZM-1", "가짜계열"), "ZZP-2": ("ZZM-2", "가짜계열둘")}


def profile(node: str) -> list[dict]:
    t, _, c = node.partition(":")
    if c not in MODS:
        return []
    mc, mn = MODS[c]
    return [row("g_profile", f"GPF-{c}", subject_table=t, subject_code=c, name_ko=c, modality_code=mc, modality_name=mn,
                mechanism_name="가짜원리", overview="가짜개요")]


def prow(gid: str, a: str, other: tuple[str, str, str], cls: str = "보완", same_day: str = "", goal: str = "") -> dict:
    at, _, ac = a.partition(":")
    return row("g_pair", gid, from_table=at, from_code=ac, from_name="가짜a", to_table=other[0], to_code=other[1],
               to_name=other[2], question_class=cls, same_day=same_day, goal_effects=goal, a_side="from",
               a_layer=lay("자기", at, ac, "가짜a"), b_layer=None)


PAIR = {
    ("product:ZZR-1", "보완"): [prow("GPR-90001", "product:ZZR-1", ("modality", "ZZM-1", "가짜계열"), goal="가짜효과"),
                              prow("GPR-90002", "product:ZZR-1", ("procedure", "ZZP-2", "가짜시술둘"), same_day="가능"),
                              prow("GPR-90003", "product:ZZR-1", ("modality", "ZZM-8", "가짜계열팔")),
                              prow("GPR-90004", "product:ZZR-1", ("modality", "ZZM-1", "가짜계열"))],
    ("product:ZZR-1", "회피,간격,순서"): [prow("GPR-90101", "product:ZZR-1", ("modality", "ZZM-1", "가짜계열"), "간격",
                                         same_day="불가"),
                                    prow("GPR-90102", "product:ZZR-1", ("modality", "ZZM-8", "가짜계열팔"), "순서")],
}


def state_hub(*, sites: dict[str, list[dict]] | None = None) -> FakeHub:
    """`sites` = {고민 코드: [{concern_code, site_code, site_name, csi_id}]} — 응답 머리의 고민 부위 칸(임시 이름)."""
    def cc_fn(p):
        code = p.get("concern_code")
        body = {"groups": CC.get(code, []), "modality_only": [], "unreached": [], "ledger_check": [],
                "inherited_from": None, "counts": {"rows": len(CC.get(code, []))}}
        if sites and code in sites:
            body["concern_sites"] = sites[code]
        return body

    def facets_fn(p):
        node, f = p.get("node", ""), p.get("facet")
        if f == "profile":
            return {"rows": profile(node), "ledger_check": []}
        if f == "indication" and not p.get("roles"):
            return {"rows": INDICATION.get(node, []), "ledger_check": []}
        return {"rows": [], "ledger_check": []}

    def pair_fn(p):
        return {"rows": PAIR.get((p.get("a"), p.get("classes") or ""), []), "same_day_packages": [], "ledger_check": []}

    def scope_fn(p):
        return {"holders": SCOPE.get(p.get("node", ""), [])}

    def coverage_fn(p):
        nodes = [x for x in (p.get("nodes") or "").split(",") if x]
        return {"request": {"nodes": nodes}, "total": len(nodes), "next_cursor": None,
                "items": [{"node": n, "name": "가짜", "kinds": [{"kind": "overview", "self": 1, "lower": 0, "inherited": 0,
                                                                 "lower_holders": 0}]} for n in nodes]}

    return FakeHub({"resolve": resolve_fn, "concern_candidates": cc_fn, "facets": facets_fn, "pair": pair_fn,
                    "scope": scope_fn, "coverage": coverage_fn, "health": {"build": {"gold_hash": "fakehash"}}},
                   limit_cap=50)


def jev(names: dict[str, float] | None = None, *, kinds: dict[str, float] | None = None, retract: dict | None = None,
        default: float = 0.9, gap: float = 0.1):
    """모의 Jev noul — 후보 이름으로 가른다(`names` · `retract`) · 종류는 `kinds`(없으면 0.1)."""
    from clinic_chat.walk.kinds import KIND_CODES

    kv = kinds if kinds is not None else {"indication": 0.9}

    def f(name, instr, state):
        if name.startswith("target_") or name.startswith("retract_"):
            i = int(name.split("_")[1])
            nm = (state.get("candidates") or [{}])[i].get("name", "")
            table = (state.get("candidates") or [{}])[i].get("table", "")
            src = (retract or {}) if name.startswith("retract_") else (names or {})
            dflt = 0.1 if name.startswith("retract_") else default
            return src.get(f"{table}:{nm}", src.get(nm, dflt))
        if name.startswith("kind_"):
            return kv.get(KIND_CODES[int(name.split("_")[1])], 0.1)
        if name == "gap":
            return gap
        return 0.1
    return f


# ───── 한 턴 도우미(가짜 CLI · 가짜 허브 · 가짜 Jev — 외부 호출 0 · 시험 설정은 켠 상태를 명시) ─────

ANSWER = "짧은 답\n되묻기?\n" + TAIL_MARK + "\n1: 1\n"


class Run:
    def __init__(self, tmp_path, fake_cli, *, hub=None, noul=None, state: str = "on", **kw) -> None:
        bin_, route, log = fake_cli
        (route / "answer.jsonl").write_text(stream(ANSWER), encoding="utf-8")
        self.tmp = tmp_path
        self.s = make_settings(tmp_path, claude_cli_bin=bin_, chat_dir=str(tmp_path / "chat"),
                               chat_cli_cwd=str(tmp_path / "cwd"), chat_state=state, **kw)
        self.hub = hub or state_hub()
        self.fj = FakeJev(noul_fn=noul or jev())
        self.log = log

        def deps(st):
            return Deps(st, self.hub.client(page_limit=50), self.fj.jev(st))
        self.app = create_app(self.s, deps_factory=deps)
        self.c = TestClient(self.app)
        self.c.__enter__()
        self.cid = re.search(r'data-conv-id="([^"]+)"', self.c.get("/").text).group(1)
        self.n = 0

    def close(self) -> None:
        self.c.__exit__(None, None, None)

    def set_noul(self, fn) -> None:
        self.fj.noul_fn = fn

    def turn(self, text: str) -> tuple[dict, list[str], str]:
        self.n += 1
        self.c.post(f"/c/{self.cid}/turns", data={"text": text})
        body = self.c.get(f"/c/{self.cid}/turns/{self.n}/events").text
        kinds = re.findall(r"^event: (\S+)$", body, re.M)
        return self.rows()[-1], kinds, body

    def rows(self, phase: str = "end") -> list[dict]:
        f = self.tmp / "chat" / "turns.jsonl"
        return [r for r in map(json.loads, f.read_text(encoding="utf-8").splitlines()) if r["phase"] == phase]
